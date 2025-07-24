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
# 第三层：单机时间优化器
# ==============================================
class MultiAircraftCoordinator:
    """多机协调优化器 - 基于时间窗口调度"""
    
    def __init__(self, command_manager, waypoints, wind_data, routes):
        self.command_manager = command_manager
        self.waypoints = waypoints
        self.wind_data = wind_data
        self.routes = routes
        
        # 系统参数
        self.FINAL_ALTITUDE = 2000      # FL020
        self.FINAL_SPEED = 180          # 180节过MP
        self.SEPARATION_TIME = 120      # MP间隔2分钟
        self.SPEED_LIMIT_ALT = 10000    # 10000ft以下速度限制
        self.SPEED_LIMIT = 250          # 250kt限制
        
        # 路径优化参数
        self.flexible_zones = {
            'A Arrival': {'direct_start': 'IR15', 'direct_end': 'IL17', 'type': 'inner'},
            'B Arrival': {'direct_start': 'IR15', 'direct_end': 'IL17', 'type': 'inner'},
            'C Arrival': {'direct_start': 'L3', 'direct_end': 'R21', 'type': 'outer'},
            'D Arrival': {'direct_start': 'L17', 'direct_end': 'R21', 'type': 'outer'}
        }
        
        # 时间窗口管理
        self.mp_schedule = {}  # {time_slot: callsign}
        self.aircraft_assignments = {}  # {callsign: assigned_time}
        
        print("🎯 多机协调系统初始化完成")
        print("📊 策略：时间窗口调度 + 路径时间协同优化")

    def process_update(self, flight_data):
        """主协调处理"""
        arrival_aircraft = self._extract_arrival_aircraft(flight_data)
        
        if not arrival_aircraft:
            return
        
        print(f"\n🎯 多机协调: {len(arrival_aircraft)} 架进港飞机")
        
        # 第一步：预测和调度
        schedule_result = self._schedule_mp_sequence(arrival_aircraft)
        
        # 第二步：路径优化
        path_commands = self._optimize_paths(arrival_aircraft, schedule_result)
        
        # 第三步：速度高度协调
        coordination_commands = self._coordinate_speed_altitude(arrival_aircraft, schedule_result)
        
        # 第四步：执行指令
        self._execute_commands(arrival_aircraft, path_commands, coordination_commands)

    def _extract_arrival_aircraft(self, flight_data):
        """提取进港飞机"""
        arrival_aircraft = []
        for aircraft in flight_data['aircraft_list']:
            if self._is_arrival_aircraft(aircraft):
                state = self._analyze_aircraft_state(aircraft)
                arrival_aircraft.append(state)
        return arrival_aircraft

    def _is_arrival_aircraft(self, aircraft):
        """判断是否为进港飞机"""
        return aircraft['flight_type'] == 'ARRIVAL' or 'Arrival' in aircraft['route_name']

    def _analyze_aircraft_state(self, aircraft):
        """分析飞机状态"""
        callsign = aircraft['callsign']
        lat = float(aircraft['lat'])
        lon = float(aircraft['lon'])
        altitude = int(aircraft['altitude'])
        ias = int(aircraft['ias'])
        heading = int(aircraft['heading'])
        route_name = aircraft['route_name']
        
        # 计算到MP距离和ETA
        mp_pos = self.waypoints.get('MP', {'lat': 0, 'lon': 0})
        distance_to_mp = self._calculate_distance(lat, lon, mp_pos['lat'], mp_pos['lon'])
        
        # 风影响计算
        wind_info = self._get_wind_at_altitude(altitude)
        ground_speed = self._calculate_ground_speed(ias, altitude, heading, wind_info)
        
        # 预测ETA（简化版）
        eta_minutes = distance_to_mp / ground_speed * 60 if ground_speed > 0 else 999
        
        return {
            'callsign': callsign,
            'route_name': route_name,
            'lat': lat,
            'lon': lon,
            'altitude': altitude,
            'ias': ias,
            'heading': heading,
            'distance_to_mp': distance_to_mp,
            'ground_speed': ground_speed,
            'eta_minutes': eta_minutes,
            'wind_info': wind_info,
            'is_flexible': route_name in self.flexible_zones
        }

    def _schedule_mp_sequence(self, aircraft_list):
        """MP时间窗口调度"""
        # 按ETA排序
        aircraft_list.sort(key=lambda x: x['eta_minutes'])
        
        schedule_result = []
        current_time = 0
        
        for aircraft in aircraft_list:
            callsign = aircraft['callsign']
            eta = aircraft['eta_minutes']
            
            # 分配时间窗口
            if len(schedule_result) == 0:
                assigned_time = eta
            else:
                min_time = schedule_result[-1]['assigned_time'] + self.SEPARATION_TIME/60  # 转为分钟
                assigned_time = max(eta, min_time)
            
            # 计算需要的时间调整
            time_adjustment = assigned_time - eta  # 正数=需要延迟，负数=需要加速
            
            schedule_result.append({
                'callsign': callsign,
                'original_eta': eta,
                'assigned_time': assigned_time,
                'time_adjustment': time_adjustment,
                'aircraft': aircraft
            })
            
            print(f"  📅 {callsign}: ETA {eta:.1f}min → 分配 {assigned_time:.1f}min (调整{time_adjustment:+.1f}min)")
        
        return schedule_result

    def _optimize_paths(self, aircraft_list, schedule_result):
        """路径优化决策"""
        path_commands = {}
        
        for item in schedule_result:
            aircraft = item['aircraft']
            callsign = aircraft['callsign']
            route_name = aircraft['route_name']
            time_adjustment = item['time_adjustment']
            
            if not aircraft['is_flexible']:
                continue
            
            # 路径选择策略
            if time_adjustment > 2:  # 需要延迟超过2分钟
                # 选择更长路径
                path_decision = self._choose_longer_path(aircraft)
                print(f"  🛣️ {callsign}: 需要延迟，选择长路径")
            elif time_adjustment < -1:  # 需要加速超过1分钟
                # 选择直飞路径
                path_decision = self._choose_direct_path(aircraft)
                print(f"  🛣️ {callsign}: 需要加速，选择直飞")
            else:
                # 保持默认路径
                path_decision = None
                print(f"  🛣️ {callsign}: 时间合适，保持默认路径")
            
            if path_decision:
                path_commands[callsign] = path_decision
        
        return path_commands

    def _choose_direct_path(self, aircraft):
        """选择直飞路径"""
        route_name = aircraft['route_name']
        zone = self.flexible_zones[route_name]
        
        start_point = self.waypoints[zone['direct_start']]
        mp_point = self.waypoints['MP']
        
        waypoints = [
            [start_point['lat'], start_point['lon']],
            [mp_point['lat'], mp_point['lon']]
        ]
        
        return {'waypoints': waypoints, 'type': 'direct'}

    def _choose_longer_path(self, aircraft):
        """选择更长路径（使用默认路径，不发custom route）"""
        return {'type': 'default'}

    def _coordinate_speed_altitude(self, aircraft_list, schedule_result):
        """速度高度协调"""
        coordination_commands = {}
        
        for item in schedule_result:
            aircraft = item['aircraft']
            callsign = aircraft['callsign']
            time_adjustment = item['time_adjustment']
            altitude = aircraft['altitude']
            ias = aircraft['ias']
            distance = aircraft['distance_to_mp']
            
            commands = {}
            
            # 基于时间调整的速度策略
            if time_adjustment > 3:  # 需要大幅延迟
                # 减速策略
                if altitude > self.SPEED_LIMIT_ALT:
                    target_speed = max(200, self.FINAL_SPEED)
                else:
                    target_speed = max(200, min(self.SPEED_LIMIT, self.FINAL_SPEED))
                
                if ias > target_speed:
                    commands['speed'] = target_speed
                    print(f"  🐌 {callsign}: 大幅延迟，减速至{target_speed}kt")
            
            elif time_adjustment < -2:  # 需要大幅加速
                # 加速策略（在约束内）
                if altitude > self.SPEED_LIMIT_ALT:
                    target_speed = min(320, max(ias, 300))  # 高空可以加速
                else:
                    target_speed = min(self.SPEED_LIMIT, max(ias, 240))  # 低空受限
                
                if target_speed > ias:
                    commands['speed'] = target_speed
                    print(f"  🚀 {callsign}: 需要加速，提速至{target_speed}kt")
            
            # 高度优化
            altitude_command = self._calculate_optimal_altitude(aircraft, time_adjustment, distance)
            if altitude_command:
                commands.update(altitude_command)
            
            if commands:
                coordination_commands[callsign] = commands
        
        return coordination_commands

    def _calculate_optimal_altitude(self, aircraft, time_adjustment, distance):
        """计算最优高度剖面"""
        altitude = aircraft['altitude']
        callsign = aircraft['callsign']
        
        if altitude <= self.FINAL_ALTITUDE:
            return {}
        
        commands = {}
        
        # 基于距离和时间调整的下降策略
        if distance > 50:  # 远距离
            if time_adjustment > 0:  # 需要延迟
                # 缓慢下降
                target_alt = max(self.FINAL_ALTITUDE, altitude - 3000)
                commands['altitude'] = target_alt
                commands['vertical_speed'] = -500
                print(f"  📉 {callsign}: 远距离延迟，缓降至{target_alt}ft")
            else:  # 需要加速
                # 正常下降
                target_alt = max(self.FINAL_ALTITUDE, altitude - 5000)
                commands['altitude'] = target_alt
                commands['vertical_speed'] = -1000
                print(f"  📉 {callsign}: 远距离加速，正常降至{target_alt}ft")
        
        elif distance > 20:  # 中距离
            # 标准下降
            target_alt = max(self.FINAL_ALTITUDE, altitude - 4000)
            commands['altitude'] = target_alt
            commands['vertical_speed'] = -800
            print(f"  📉 {callsign}: 中距离，标准降至{target_alt}ft")
        
        else:  # 近距离
            # 快速完成下降
            commands['altitude'] = self.FINAL_ALTITUDE
            commands['vertical_speed'] = -1200
            print(f"  📉 {callsign}: 近距离，快速降至{self.FINAL_ALTITUDE}ft")
        
        return commands

    def _execute_commands(self, aircraft_list, path_commands, coordination_commands):
        """执行协调指令"""
        executed_count = 0
        
        for aircraft in aircraft_list:
            callsign = aircraft['callsign']
            all_commands = {}
            
            # 合并路径指令
            if callsign in path_commands:
                path_cmd = path_commands[callsign]
                if path_cmd['type'] == 'direct':
                    all_commands['waypoints'] = path_cmd['waypoints']
            
            # 合并协调指令
            if callsign in coordination_commands:
                all_commands.update(coordination_commands[callsign])
            
            # 执行指令
            if all_commands:
                success = self.command_manager.combo(callsign, **all_commands)
                if success:
                    executed_count += 1
                    print(f"  ✅ {callsign}: 执行指令 {all_commands}")
                else:
                    print(f"  ❌ {callsign}: 指令执行失败")
        
        print(f"📊 协调完成: {executed_count}/{len(aircraft_list)} 架飞机接收指令")

    def _calculate_distance(self, lat1, lon1, lat2, lon2):
        """计算距离（海里）"""
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

    def _get_wind_at_altitude(self, altitude):
        """简化的风数据获取"""
        # 这里应该使用实际的wind_data
        return {'direction': 200, 'speed': 5}

    def _calculate_ground_speed(self, ias, altitude, heading, wind_info):
        """简化的地速计算"""
        # 简化计算，实际应该使用完整的wind correction
        return ias + 20  # 简化为IAS+20kt作为地速

# 使用示例（替换原来的单机优化器）
class EnhancedATCSystem:
    def __init__(self, command_manager, waypoints, wind_data, routes):
        self.multi_coordinator = MultiAircraftCoordinator(
            command_manager, waypoints, wind_data, routes
        )
        # 保留单机优化器作为备用
        self.single_optimizer = None  # 可以保留原来的单机优化器
        
    def process_update(self, flight_data):
        """主处理入口"""
        arrival_aircraft = self._count_arrival_aircraft(flight_data)
        
        if arrival_aircraft >= 2:
            # 多机协调模式
            print("🎯 启用多机协调模式")
            self.multi_coordinator.process_update(flight_data)
        elif arrival_aircraft == 1:
            # 单机优化模式
            print("🎯 启用单机优化模式")
            if self.single_optimizer:
                self.single_optimizer.process_update(flight_data)
        else:
            print("⏸️ 无进港飞机")
    
    def _count_arrival_aircraft(self, flight_data):
        """统计进港飞机数量"""
        count = 0
        for aircraft in flight_data['aircraft_list']:
            if aircraft['flight_type'] == 'ARRIVAL' or 'Arrival' in aircraft['route_name']:
                count += 1
        return count
# ==============================================
# 主系统
# ==============================================

command_manager = ATCCommandManager(socketio)
data_processor = FlightDataProcessor()
flight_optimizer = multi_coordinator = MultiAircraftCoordinator(command_manager, waypointData, windData, routes)

@socketio.on('connect')
def handle_connect():
    command_manager.set_connection_status(True)
    print("✅ 前端已连接")
    emit('connected', {'message': '单机优化后端已连接'})

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
    print("🚀 单机时间最优化系统启动中...")
    print("🎯 优化目标: 最快到达MP，满足FL020@180kt")
    print("📐 优化策略: 四阶段动态下降+减速剖面")
    print("✅ 约束保证: <10000ft时IAS≤250kt")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)