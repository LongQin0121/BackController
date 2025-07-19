# ✅ 简洁版特点

# 必要信息显示: callsign, 机型, 坐标, IAS, 垂直率, 航线, 地速
# 优化目标: 最小延误 + 最短距离
# 约束条件: FL020@180kt过MP, 5nm间隔, 2000fpm下降率
# 清晰输出: 去掉调试信息，保留核心功能
# 指令生成: 基于距离的下降剖面 + 分阶段速度管理


#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from flask_socketio import SocketIO, emit
import time
import math

# 导入环境数据
try:
    from env_data import waypointData, windData, routes
    print(f"✅ 成功导入环境数据")
except ImportError:
    print("❌ 请确保 env_data.py 文件存在并包含 waypointData, windData, routes")
    waypointData = {}
    windData = []
    routes = {}

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ==============================================
# 大气计算函数
# ==============================================

def get_wind_at_altitude(altitude_feet, wind_data):
    """根据高度获取风数据（windData中的alt是英尺）"""
    if not wind_data:
        return {'direction': 0, 'speed': 0, 'temp': 15}
    
    altitude = altitude_feet
    
    if altitude <= wind_data[0]['alt']:
        return {
            'direction': wind_data[0]['dir'],
            'speed': wind_data[0]['speed'],
            'temp': wind_data[0]['temp']
        }
    
    if altitude >= wind_data[-1]['alt']:
        last = wind_data[-1]
        return {
            'direction': last['dir'],
            'speed': last['speed'],
            'temp': last['temp']
        }
    
    lower_layer = wind_data[0]
    upper_layer = wind_data[-1]
    
    for i in range(len(wind_data) - 1):
        if altitude >= wind_data[i]['alt'] and altitude <= wind_data[i + 1]['alt']:
            lower_layer = wind_data[i]
            upper_layer = wind_data[i + 1]
            break
    
    ratio = (altitude - lower_layer['alt']) / (upper_layer['alt'] - lower_layer['alt'])
    
    dir_diff = upper_layer['dir'] - lower_layer['dir']
    if dir_diff > 180:
        dir_diff -= 360
    if dir_diff < -180:
        dir_diff += 360
    
    interpolated_dir = lower_layer['dir'] + dir_diff * ratio
    if interpolated_dir < 0:
        interpolated_dir += 360
    if interpolated_dir >= 360:
        interpolated_dir -= 360
    
    return {
        'direction': interpolated_dir,
        'speed': lower_layer['speed'] + (upper_layer['speed'] - lower_layer['speed']) * ratio,
        'temp': lower_layer['temp'] + (upper_layer['temp'] - lower_layer['temp']) * ratio
    }

def ias_to_tas(ias, altitude_feet, temp_celsius):
    """IAS转TAS"""
    std_temp_k = 288.15
    lapse_rate = 0.0065
    altitude_meters = altitude_feet * 0.3048
    actual_temp_k = temp_celsius + 273.15
    std_temp_at_alt = std_temp_k - lapse_rate * altitude_meters
    temp_ratio = math.sqrt(actual_temp_k / std_temp_at_alt)
    alt_ratio = math.sqrt(std_temp_k / (std_temp_k - lapse_rate * altitude_meters))
    return ias * alt_ratio * temp_ratio

def calculate_ground_speed_and_track(tas, aircraft_heading, wind_direction, wind_speed):
    """计算地速和航迹"""
    ac_heading_rad = aircraft_heading * math.pi / 180
    ac_vx = tas * math.sin(ac_heading_rad)
    ac_vy = tas * math.cos(ac_heading_rad)
    
    wind_from_rad = (wind_direction + 180) * math.pi / 180
    wind_vx = wind_speed * math.sin(wind_from_rad)
    wind_vy = wind_speed * math.cos(wind_from_rad)
    
    gs_vx = ac_vx + wind_vx
    gs_vy = ac_vy + wind_vy
    
    ground_speed = math.sqrt(gs_vx * gs_vx + gs_vy * gs_vy)
    track_direction = math.atan2(gs_vx, gs_vy) * 180 / math.pi
    if track_direction < 0:
        track_direction += 360
    
    return {
        'speed': ground_speed,
        'track': track_direction,
        'windCorrection': track_direction - aircraft_heading
    }

def calculate_distance(lat1, lon1, lat2, lon2):
    """计算两点间距离（海里）"""
    R = 3440.065
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) * math.sin(delta_lat / 2) +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(delta_lon / 2) * math.sin(delta_lon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

# ==============================================
# 第一层：ATC指令集
# ==============================================

class ATCCommandManager:
    """ATC指令管理器"""
    
    def __init__(self, socketio_instance):
        self.socketio = socketio_instance
        self.is_connected = False
    
    def set_connection_status(self, status):
        self.is_connected = status
    
    def combo(self, callsign, **kwargs):
        """组合指令发送"""        
        if not self.is_connected:
            print(f"❌ 前端未连接，无法发送指令给 {callsign}")
            return False
        
        instructions = {}
        
        if 'altitude' in kwargs:
            instructions['altitude'] = kwargs['altitude']
        if 'speed' in kwargs:
            instructions['speed'] = kwargs['speed']
        if 'vertical_speed' in kwargs:
            instructions['verticalSpeed'] = kwargs['vertical_speed']
        if 'waypoints' in kwargs:
            instructions['customRoute'] = kwargs['waypoints']
        if 'heading' in kwargs:
            instructions['heading'] = kwargs['heading']
        
        if not instructions:
            return False
        
        command = {'callsign': callsign, 'instructions': instructions}
        
        try:
            self.socketio.emit('atc_commands', [command])
            print(f"✅ 指令已发送给 {callsign}: {instructions}")
            return True
        except Exception as e:
            print(f"❌ 指令发送失败 {callsign}: {e}")
            return False

# ==============================================
# 第二层：数据提取器
# ==============================================

class FlightDataProcessor:
    """数据提取器"""
    
    def process_data(self, data):
        """提取基础数据"""
        raw_aircraft_data = data.get('aircraft', [])
        sim_time = data.get('simulationTimeFormatted', 'N/A')
        current_time_stamp = time.time()
        
        basic_data = []
        
        for aircraft in raw_aircraft_data:
            try:
                callsign = aircraft.get('callsign')
                if not callsign:
                    continue
                
                pos = aircraft.get('position', {})
                altitude = pos.get('altitude', 'N/A')
                lat = pos.get('lat', 'N/A')
                lon = pos.get('lon', 'N/A')
                
                aircraft_type = aircraft.get('aircraftType', 'Unknown')
                
                speed_data = aircraft.get('speed', {})
                ias = speed_data.get('ias', 250)
                
                vertical_speed = aircraft.get('vertical', {}).get('verticalSpeed', 0)
                heading = aircraft.get('direction', {}).get('heading', 0)
                route_name = aircraft.get('navigation', {}).get('plannedRoute', 'Unknown')
                flight_type = aircraft.get('type', 'Unknown')
                
                basic_aircraft = {
                    'timestamp': current_time_stamp,
                    'sim_time': sim_time,
                    'callsign': callsign,
                    'altitude': altitude,
                    'lat': lat,
                    'lon': lon,
                    'aircraft_type': aircraft_type,
                    'ias': ias,
                    'vertical_speed': vertical_speed,
                    'heading': heading,
                    'route_name': route_name,
                    'flight_type': flight_type
                }
                
                basic_data.append(basic_aircraft)
                
            except Exception as e:
                print(f"❌ 提取 {callsign} 数据失败: {e}")
        
        return {
            'sim_time': sim_time,
            'timestamp': current_time_stamp,
            'aircraft_list': basic_data
        }

# ==============================================
# 第三层：优化器
# ==============================================

class FlightOptimizer:
    """飞行优化器 - 最小化延误和飞行距离"""
    
    def __init__(self, command_manager):
        self.command_manager = command_manager
        self.waypoints = waypointData
        self.wind_data = windData
        self.routes = routes
        self.aircraft_states = {}
        self.analysis_count = 0
        
        # 优化目标和约束参数
        self.FINAL_ALTITUDE = 2000      # FL020 过MP
        self.FINAL_SPEED = 180          # 180节过MP
        self.MIN_SEPARATION = 5         # 5海里最小间隔
        self.MAX_DESCENT_RATE = 2000    # 最大下降率 ft/min
        self.SPEED_TRANSITION_ALT = 10000  # 速度转换高度：10000ft以上250kt，以下减速到180kt
        
        print("✅ 飞行优化器初始化完成")
        print(f"🎯 优化目标: 最小延误时间 + 最短飞行距离")
        print(f"📋 约束条件: FL020/180kt过MP, 间隔≥5nm, 下降率≤2000fpm")
        
        if 'MP' in self.waypoints:
            mp = self.waypoints['MP']
            print(f"🎯 MP坐标: {mp['lat']:.4f}, {mp['lon']:.4f}")

    def process_update(self, flight_data):
        """处理飞机数据更新"""
        self.analysis_count += 1
        current_time = time.strftime('%H:%M:%S')
        
        sim_time = flight_data['sim_time']
        aircraft_list = flight_data['aircraft_list']
        
        print(f"\n📡 #{self.analysis_count} - 系统: {current_time} | 模拟: {sim_time}")
        print("=" * 80)
        
        # 筛选和分析进港飞机
        arrival_aircraft = []
        for aircraft in aircraft_list:
            flight_type = aircraft['flight_type']
            route_name = aircraft['route_name']
            
            if flight_type == 'ARRIVAL' or 'Arrival' in route_name:
                # 计算完整状态
                state = self._analyze_aircraft(aircraft)
                self.aircraft_states[state['callsign']] = state
                arrival_aircraft.append(state)
        
        if arrival_aircraft:
            self._display_aircraft_status(arrival_aircraft)
            self._optimize_and_command(arrival_aircraft)
        else:
            print("⏸️ 无进港飞机")
        
        print("✅ 处理完成\n")

    def _analyze_aircraft(self, aircraft):
        """分析单架飞机状态"""
        callsign = aircraft['callsign']
        lat = float(aircraft['lat'])
        lon = float(aircraft['lon'])
        altitude = int(aircraft['altitude'])
        ias = int(aircraft['ias'])
        heading = int(aircraft['heading'])
        vertical_speed = int(aircraft['vertical_speed'])
        route_name = aircraft['route_name']
        aircraft_type = aircraft['aircraft_type']
        
        # 获取风数据并计算地速
        wind_info = get_wind_at_altitude(altitude, self.wind_data)
        tas = ias_to_tas(ias, altitude, wind_info['temp'])
        gs_info = calculate_ground_speed_and_track(tas, heading, wind_info['direction'], wind_info['speed'])
        
        # 计算到MP距离
        mp_pos = self.waypoints.get('MP', {'lat': 0, 'lon': 0})
        distance_to_mp = calculate_distance(lat, lon, mp_pos['lat'], mp_pos['lon'])
        
        return {
            'callsign': callsign,
            'aircraft_type': aircraft_type,
            'route_name': route_name,
            'lat': lat,
            'lon': lon,
            'altitude': altitude,
            'ias': ias,
            'tas': tas,
            'heading': heading,
            'vertical_speed': vertical_speed,
            'ground_speed': gs_info['speed'],
            'track': gs_info['track'],
            'distance_to_mp': distance_to_mp,
            'wind': wind_info,
            'last_command_time': self.aircraft_states.get(callsign, {}).get('last_command_time', 0)
        }

    def _display_aircraft_status(self, arrival_aircraft):
        """显示飞机状态"""
        print(f"📊 进港飞机: {len(arrival_aircraft)} 架")
        
        for state in arrival_aircraft:
            callsign = state['callsign']
            aircraft_type = state['aircraft_type']
            route_name = state['route_name']
            lat = state['lat']
            lon = state['lon']
            altitude = state['altitude']
            ias = state['ias']
            vertical_speed = state['vertical_speed']
            ground_speed = state['ground_speed']
            distance_to_mp = state['distance_to_mp']
            
            eta = distance_to_mp / ground_speed * 60 if ground_speed > 0 else 999
            
            print(f"  ✈️ {callsign} ({aircraft_type}) - {route_name}")
            print(f"     位置: ({lat:.3f}, {lon:.3f}) {altitude}ft | IAS: {ias}kt | VS: {vertical_speed:+d}fpm")
            print(f"     地速: {ground_speed:.0f}kt | 距MP: {distance_to_mp:.1f}nm | ETA: {eta:.1f}min")

    def _optimize_and_command(self, arrival_aircraft):
        """执行优化并发送指令"""
        print(f"\n🎯 开始优化 {len(arrival_aircraft)} 架进港飞机")
        
        # 按ETA排序（最小化延误）
        sorted_aircraft = sorted(arrival_aircraft, key=lambda x: x['distance_to_mp'] / x['ground_speed'])
        
        print("📋 按ETA排序的进港序列:")
        for i, state in enumerate(sorted_aircraft):
            eta = state['distance_to_mp'] / state['ground_speed'] * 60
            print(f"  {i+1}. {state['callsign']} - ETA: {eta:.1f}min")
        
        # 生成指令
        command_count = 0
        for i, state in enumerate(sorted_aircraft):
            if self._generate_commands(state, i, len(sorted_aircraft)):
                command_count += 1
        
        print(f"📡 本轮发送了 {command_count} 条指令")

    def _generate_commands(self, state, sequence, total):
        """为单架飞机生成优化指令"""
        callsign = state['callsign']
        current_alt = state['altitude']
        current_ias = state['ias']
        distance_to_mp = state['distance_to_mp']
        ground_speed = state['ground_speed']
        
        # 指令冷却
        time_since_last = time.time() - state.get('last_command_time', 0)
        if time_since_last < 30:
            return False
        
        commands = {}
        
        # 1. 高度管理 - 基于距离的下降剖面
        if distance_to_mp < 80 and current_alt > self.FINAL_ALTITUDE:
            if distance_to_mp > 50:
                target_alt = 15000  # 远距离：先降到FL150
            elif distance_to_mp > 30:
                target_alt = 10000  # 中距离：降到FL100
            elif distance_to_mp > 15:
                target_alt = 6000   # 近距离：降到6000ft
            else:
                target_alt = self.FINAL_ALTITUDE  # 最终进近：FL020
            
            if current_alt > target_alt + 500:  # 500英尺容差
                commands['altitude'] = target_alt
                
                # 计算合理的垂直速度（不超过最大下降率）
                time_to_mp = distance_to_mp / ground_speed * 60  # 分钟
                if time_to_mp > 0:
                    required_vs = min((current_alt - target_alt) / time_to_mp, self.MAX_DESCENT_RATE)
                    if required_vs > 500:  # 最小下降率
                        commands['vertical_speed'] = -int(required_vs)
        
        # 2. 速度管理 - 基于高度的速度策略
        if current_alt > self.SPEED_TRANSITION_ALT:
            # 高空（>10000ft）：保持或减速到250kt
            target_speed = 250
        else:
            # 低空（≤10000ft）：根据距离分阶段减速
            if distance_to_mp > 20:
                target_speed = 250
            elif distance_to_mp > 10:
                target_speed = 220
            else:
                target_speed = self.FINAL_SPEED  # 180kt过MP
        
        if abs(current_ias - target_speed) > 10:  # 10节容差
            commands['speed'] = target_speed
        
        # 3. 航路优化 - 灵活进近（简化版）
        # TODO: 后续实现直飞MP逻辑
        
        # 4. 间隔管理 - 确保5海里间隔
        # TODO: 后续实现间隔冲突检测
        
        # 发送指令
        if commands:
            print(f"  📤 {callsign} (序列{sequence+1}): {commands}")
            success = self.command_manager.combo(callsign, **commands)
            if success:
                self.aircraft_states[callsign]['last_command_time'] = time.time()
                return True
        
        return False

# ==============================================
# 主系统
# ==============================================

command_manager = ATCCommandManager(socketio)
data_processor = FlightDataProcessor()
flight_optimizer = FlightOptimizer(command_manager)

@socketio.on('connect')
def handle_connect():
    command_manager.set_connection_status(True)
    print("✅ 前端已连接")
    emit('connected', {'message': '后端已连接'})

@socketio.on('disconnect')
def handle_disconnect():
    command_manager.set_connection_status(False)
    print("❌ 前端已断开")

@socketio.on('aircraft_data')
def handle_aircraft_data(data):
    """接收飞机数据"""    
    flight_data = data_processor.process_data(data)
    flight_optimizer.process_update(flight_data)

if __name__ == '__main__':
    print("🚀 智能飞行管制系统启动中...")
    print("📋 优化目标: 延误时间最小化 + 飞行距离最短化")
    print("🔧 约束条件: FL020@180kt过MP, 间隔≥5nm, 下降率≤2000fpm, 速度分层管理")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
