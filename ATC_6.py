#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from flask_socketio import SocketIO, emit
import time
import math
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# 导入环境数据
try:
    from env_data import waypointData, windData, routes
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

def get_wind_at_altitude(altitude_feet: float, wind_data: List[Dict]) -> Dict:
    """根据高度获取风数据（插值）"""
    if not wind_data:
        return {'direction': 0, 'speed': 0, 'temp': 15}
        
    altitude_meters = altitude_feet * 0.3048
    
    if altitude_meters <= wind_data[0]['alt']:
        return {
            'direction': wind_data[0]['dir'],
            'speed': wind_data[0]['speed'],
            'temp': wind_data[0]['temp']
        }
    
    if altitude_meters >= wind_data[-1]['alt']:
        last = wind_data[-1]
        return {
            'direction': last['dir'],
            'speed': last['speed'],
            'temp': last['temp']
        }
    
    # 找到上下层
    lower_layer = wind_data[0]
    upper_layer = wind_data[-1]
    
    for i in range(len(wind_data) - 1):
        if wind_data[i]['alt'] <= altitude_meters <= wind_data[i + 1]['alt']:
            lower_layer = wind_data[i]
            upper_layer = wind_data[i + 1]
            break
    
    # 插值比例
    ratio = (altitude_meters - lower_layer['alt']) / (upper_layer['alt'] - lower_layer['alt'])
    
    # 风向插值（处理圆形特性）
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

def ias_to_tas(ias: float, altitude_feet: float, temp_celsius: float) -> float:
    """指示空速转真空速"""
    std_temp_k = 288.15
    lapse_rate = 0.0065
    altitude_meters = altitude_feet * 0.3048
    actual_temp_k = temp_celsius + 273.15
    std_temp_at_alt = std_temp_k - lapse_rate * altitude_meters
    
    temp_ratio = math.sqrt(actual_temp_k / std_temp_at_alt)
    alt_ratio = math.sqrt(std_temp_k / (std_temp_k - lapse_rate * altitude_meters))
    
    return ias * alt_ratio * temp_ratio

def calculate_ground_speed_and_track(tas: float, aircraft_heading: float, wind_direction: float, wind_speed: float) -> Dict:
    """计算地速和航迹"""
    ac_heading_rad = math.radians(aircraft_heading)
    ac_vx = tas * math.sin(ac_heading_rad)
    ac_vy = tas * math.cos(ac_heading_rad)
    
    wind_from_rad = math.radians(wind_direction + 180)
    wind_vx = wind_speed * math.sin(wind_from_rad)
    wind_vy = wind_speed * math.cos(wind_from_rad)
    
    gs_vx = ac_vx + wind_vx
    gs_vy = ac_vy + wind_vy
    
    ground_speed = math.sqrt(gs_vx * gs_vx + gs_vy * gs_vy)
    track_direction = math.degrees(math.atan2(gs_vx, gs_vy))
    if track_direction < 0:
        track_direction += 360
    
    return {
        'speed': ground_speed,
        'track': track_direction,
        'windCorrection': track_direction - aircraft_heading
    }

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间距离（海里）"""
    R = 3440.065  # 地球半径（海里）
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
        """
        根据输入参数自动组合指令
        
        参数:
            callsign (str): 航班呼号
            **kwargs: 指令参数
            
        支持的参数:
            altitude: 高度 (int/str)
            speed: 速度 (int)
            vertical_speed: 垂直速度 (int)
            waypoints: 航路点列表 (list)
          
        使用示例:
            # 单个指令
            combo('CSN202', altitude=10000)
            
            # 两个参数组合
            combo('CSN202', altitude=10000, speed=250)
            
            # 3个参数组合
            combo('CSN202', altitude=8000, speed=220, vertical_speed=-800)
            
            # 航路组合
            combo('CSN202', waypoints=['IR15', 'IR5', 'MP'], altitude=8000)
            
        """
       
        if not self.is_connected:
            print(f"❌ 前端未连接，无法发送指令给 {callsign}")
            return False
        
        instructions = {}
        
        # 处理各种参数
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
            print(f"❌ 没有有效的指令参数")
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
# 第二层：数据清洗器
# ==============================================

class FlightDataProcessor:
        """数据清洗器"""
        
        def process_data(self, data):
            """清洗飞机数据"""
            raw_aircraft_data = data.get('aircraft', [])
            cleaned_data = []
            
            for aircraft in raw_aircraft_data:
                try:
                    cleaned = self._clean_single_aircraft(aircraft)
                    if cleaned:
                        cleaned_data.append(cleaned)
                except Exception as e:
                    print(f"❌ 清洗飞机数据失败: {e}")
                    cleaned_data.append(aircraft)  # 如果清洗失败，使用原始数据
            
            return cleaned_data
            
        def _clean_single_aircraft(self, aircraft):
            """清洗单架飞机数据 - 完整版"""
            cleaned = aircraft.copy()
            
            # 🔧 1. 清洗位置数据
            if 'position' in cleaned:
                pos = cleaned['position']
                if isinstance(pos, dict):
                    # 纬度
                    if 'lat' in pos and pos['lat'] is not None:
                        try:
                            pos['lat'] = float(pos['lat'])
                        except (ValueError, TypeError):
                            print(f"❌ 纬度转换失败: {pos['lat']}")
                    
                    # 经度
                    if 'lon' in pos and pos['lon'] is not None:
                        try:
                            pos['lon'] = float(pos['lon'])
                        except (ValueError, TypeError):
                            print(f"❌ 经度转换失败: {pos['lon']}")
                    
                    # 高度
                    if 'altitude' in pos and pos['altitude'] is not None:
                        try:
                            pos['altitude'] = int(pos['altitude'])
                        except (ValueError, TypeError):
                            print(f"❌ 高度转换失败: {pos['altitude']}")
            
            # 🔧 2. 清洗速度数据
            if 'speed' in cleaned:
                speed = cleaned['speed']
                if isinstance(speed, dict):
                    speed_keys = ['ias', 'tas', 'groundSpeed']
                    for key in speed_keys:
                        if key in speed and speed[key] is not None:
                            try:
                                speed[key] = int(speed[key])
                            except (ValueError, TypeError):
                                print(f"❌ {key}转换失败: {speed[key]}")
            
            # 🔧 3. 清洗方向数据
            if 'direction' in cleaned:
                direction = cleaned['direction']
                if isinstance(direction, dict):
                    direction_keys = ['heading', 'track']
                    for key in direction_keys:
                        if key in direction and direction[key] is not None:
                            try:
                                direction[key] = int(direction[key])
                            except (ValueError, TypeError):
                                print(f"❌ {key}转换失败: {direction[key]}")
            
            # 🔧 4. 清洗垂直数据
            if 'vertical' in cleaned:
                vertical = cleaned['vertical']
                if isinstance(vertical, dict):
                    # 垂直速度
                    if 'verticalSpeed' in vertical and vertical['verticalSpeed'] is not None:
                        try:
                            vertical['verticalSpeed'] = int(vertical['verticalSpeed'])
                        except (ValueError, TypeError):
                            print(f"❌ 垂直速度转换失败: {vertical['verticalSpeed']}")
                    
                    # 目标高度
                    if 'targetAltitude' in vertical and vertical['targetAltitude'] is not None:
                        try:
                            vertical['targetAltitude'] = int(vertical['targetAltitude'])
                        except (ValueError, TypeError):
                            print(f"❌ 目标高度转换失败: {vertical['targetAltitude']}")
            
            # 🔧 5. 清洗风信息数据
            if 'wind' in cleaned:
                wind = cleaned['wind']
                if isinstance(wind, dict) and wind:
                    # 风向
                    if 'direction' in wind and wind['direction'] is not None:
                        try:
                            wind['direction'] = int(wind['direction'])
                        except (ValueError, TypeError):
                            print(f"❌ 风向转换失败: {wind['direction']}")
                    
                    # 风速
                    if 'speed' in wind and wind['speed'] is not None:
                        try:
                            wind['speed'] = int(wind['speed'])
                        except (ValueError, TypeError):
                            print(f"❌ 风速转换失败: {wind['speed']}")
                    
                    # 温度
                    if 'temp' in wind and wind['temp'] is not None:
                        try:
                            wind['temp'] = float(wind['temp'])  # 温度可能有小数
                        except (ValueError, TypeError):
                            print(f"❌ 温度转换失败: {wind['temp']}")
            
            # 🔧 6. 清洗灵活进近数据
            if 'flexibleApproach' in cleaned:
                flexible = cleaned['flexibleApproach']
                if isinstance(flexible, dict) and 'distances' in flexible:
                    distances = flexible['distances']
                    if isinstance(distances, dict):
                        # 清洗所有4个距离字段
                        distance_keys = [
                            'currentDirectToMP',      # 当前直飞MP距离
                            'earliestDistanceToMP',   # 最早到达MP距离
                            'latestDistanceToMP',     # 最晚到达MP距离
                            'customRouteRemaining'    # 自定义航路剩余距离
                        ]
                        
                        for key in distance_keys:
                            if key in distances and distances[key] is not None:
                                try:
                                    value = str(distances[key]).replace('nm', '').strip()
                                    distances[key] = float(value)
                                except (ValueError, TypeError):
                                    print(f"❌ {key}转换失败: {distances[key]}")
            
            return cleaned

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
        self.last_update_time = time.time()
        
        # 灵活进近区域定义
        self.flexible_zones = {
            'A Arrival': {'start': 'IR15', 'end': 'IL17'},
            'B Arrival': {'start': 'IR15', 'end': 'IL17'},
            'C Arrival': {'start': 'L3', 'end': 'R21'},
            'D Arrival': {'start': 'L17', 'end': 'R21'}
        }
        
        # 约束参数
        self.FINAL_ALTITUDE = 2000  # FL020
        self.FINAL_SPEED = 180      # 180节过MP
        self.MIN_SEPARATION = 5     # 5海里间隔
        self.MAX_DESCENT_RATE = 2000  # 最大下降率 ft/min
        self.SPEED_TRANSITION_ALT = 10000  # 速度转换高度
        
        print("✅ 飞行优化器初始化完成")
        print(f"📍 加载航路点: {len(self.waypoints)} 个")
        print(f"🌪️ 加载风数据: {len(self.wind_data)} 层")
        print(f"🛣️ 加载航线: {len(self.routes)} 条")

    def process_update(self, aircraft_data: List[Dict]):
        """处理飞机数据更新"""
        current_time = time.time()
        dt = current_time - self.last_update_time
        
        # 更新飞机状态
        for aircraft in aircraft_data:
            callsign = aircraft.get('callsign')
            if callsign:
                self.aircraft_states[callsign] = self._analyze_aircraft(aircraft)
        
        # 执行优化决策
        self._optimize_and_command(dt)
        
        self.last_update_time = current_time

    def _analyze_aircraft(self, aircraft: Dict) -> Dict:
        """分析单架飞机状态"""
        callsign = aircraft.get('callsign')
        route_name = aircraft.get('route', {}).get('name', '')
        
        # 基础信息
        pos = aircraft.get('position', {})
        current_lat = pos.get('lat', 0)
        current_lon = pos.get('lon', 0)
        current_alt = pos.get('altitude', 0)
        
        speed_data = aircraft.get('speed', {})
        current_ias = speed_data.get('ias', 250)
        
        # 获取当前风数据
        wind_info = get_wind_at_altitude(current_alt, self.wind_data)
        
        # 计算真空速和地速
        tas = ias_to_tas(current_ias, current_alt, wind_info['temp'])
        direction_data = aircraft.get('direction', {})
        heading = direction_data.get('heading', 0)
        
        gs_info = calculate_ground_speed_and_track(
            tas, heading, wind_info['direction'], wind_info['speed']
        )
        
        # 计算关键距离
        distances = self._calculate_key_distances(aircraft, current_lat, current_lon)
        
        # 分析灵活进近状态
        flexible_status = self._analyze_flexible_approach(aircraft, distances)
        
        state = {
            'callsign': callsign,
            'route_name': route_name,
            'position': {'lat': current_lat, 'lon': current_lon, 'altitude': current_alt},
            'speeds': {
                'ias': current_ias,
                'tas': tas,
                'ground_speed': gs_info['speed']
            },
            'wind': wind_info,
            'distances': distances,
            'flexible_status': flexible_status,
            'last_command_time': getattr(self.aircraft_states.get(callsign, {}), 'get', lambda x, y: 0)('last_command_time', 0),
            'raw_data': aircraft
        }
        
        return state

    def _calculate_key_distances(self, aircraft: Dict, lat: float, lon: float) -> Dict:
        """计算关键距离"""
        # MP坐标
        mp_pos = self.waypoints.get('MP', {'lat': 0, 'lon': 0})
        
        # 当前到MP直线距离
        direct_to_mp = calculate_distance(lat, lon, mp_pos['lat'], mp_pos['lon'])
        
        route_name = aircraft.get('route', {}).get('name', '')
        
        if route_name not in self.routes:
            return {
                'direct_to_mp': direct_to_mp,
                'earliest_to_mp': direct_to_mp,
                'latest_to_mp': direct_to_mp,
                'remaining_route': 0
            }
        
        route_points = self.routes[route_name]
        
        # 找到当前最近的航路点
        current_waypoint_index = self._find_nearest_waypoint_index(lat, lon, route_points)
        
        # 计算最早到达距离（直飞弧线起始点）
        earliest_distance = direct_to_mp
        if route_name in self.flexible_zones:
            start_point = self.flexible_zones[route_name]['start']
            if start_point in self.waypoints:
                start_pos = self.waypoints[start_point]
                earliest_distance = (
                    calculate_distance(lat, lon, start_pos['lat'], start_pos['lon']) +
                    calculate_distance(start_pos['lat'], start_pos['lon'], mp_pos['lat'], mp_pos['lon'])
                )
        
        # 计算最晚到达距离（走完弧线）
        latest_distance = direct_to_mp
        if route_name in self.flexible_zones:
            end_point = self.flexible_zones[route_name]['end']
            if end_point in self.waypoints:
                end_pos = self.waypoints[end_point]
                latest_distance = self._calculate_route_distance(lat, lon, route_points, current_waypoint_index, end_point) + \
                                calculate_distance(end_pos['lat'], end_pos['lon'], mp_pos['lat'], mp_pos['lon'])
        
        # 剩余航路距离
        remaining_route = self._calculate_remaining_route_distance(lat, lon, route_points, current_waypoint_index)
        
        return {
            'direct_to_mp': direct_to_mp,
            'earliest_to_mp': earliest_distance,
            'latest_to_mp': latest_distance,
            'remaining_route': remaining_route
        }

    def _find_nearest_waypoint_index(self, lat: float, lon: float, route_points: List[str]) -> int:
        """找到最近的航路点索引"""
        min_distance = float('inf')
        nearest_index = 0
        
        for i, point_name in enumerate(route_points):
            if point_name in self.waypoints:
                point_pos = self.waypoints[point_name]
                distance = calculate_distance(lat, lon, point_pos['lat'], point_pos['lon'])
                if distance < min_distance:
                    min_distance = distance
                    nearest_index = i
        
        return nearest_index

    def _calculate_route_distance(self, start_lat: float, start_lon: float, 
                                route_points: List[str], start_index: int, end_point: str) -> float:
        """计算航路距离"""
        total_distance = 0
        current_lat, current_lon = start_lat, start_lon
        
        # 找到结束点索引
        try:
            end_index = route_points.index(end_point)
        except ValueError:
            return 0
        
        # 从当前位置到起始航路点
        if start_index < len(route_points) and route_points[start_index] in self.waypoints:
            start_point_pos = self.waypoints[route_points[start_index]]
            total_distance += calculate_distance(current_lat, current_lon, 
                                               start_point_pos['lat'], start_point_pos['lon'])
            current_lat, current_lon = start_point_pos['lat'], start_point_pos['lon']
        
        # 沿航路计算
        for i in range(start_index, min(end_index, len(route_points) - 1)):
            if route_points[i] in self.waypoints and route_points[i + 1] in self.waypoints:
                pos1 = self.waypoints[route_points[i]]
                pos2 = self.waypoints[route_points[i + 1]]
                total_distance += calculate_distance(pos1['lat'], pos1['lon'], pos2['lat'], pos2['lon'])
        
        return total_distance

    def _calculate_remaining_route_distance(self, lat: float, lon: float, 
                                          route_points: List[str], current_index: int) -> float:
        """计算剩余航路距离"""
        if current_index >= len(route_points) - 1:
            return 0
        
        return self._calculate_route_distance(lat, lon, route_points, current_index, 'MP')

    def _analyze_flexible_approach(self, aircraft: Dict, distances: Dict) -> Dict:
        """分析灵活进近状态"""
        route_name = aircraft.get('route', {}).get('name', '')
        
        if route_name not in self.flexible_zones:
            return {'in_flexible_zone': False, 'can_direct_mp': False}
        
        # 这里可以添加更复杂的逻辑来判断是否在灵活区域内
        # 简化版本：基于距离判断
        direct_distance = distances['direct_to_mp']
        earliest_distance = distances['earliest_to_mp']
        
        in_flexible_zone = abs(direct_distance - earliest_distance) < 10  # 10海里容差
        
        return {
            'in_flexible_zone': in_flexible_zone,
            'can_direct_mp': in_flexible_zone,
            'zone_start': self.flexible_zones[route_name]['start'],
            'zone_end': self.flexible_zones[route_name]['end']
        }

    def _optimize_and_command(self, dt: float):
        """执行优化并发送指令"""
        arrival_aircraft = {k: v for k, v in self.aircraft_states.items() 
                          if 'Arrival' in v.get('route_name', '')}
        
        if not arrival_aircraft:
            return
        
        # 按到达MP的预计时间排序
        sorted_aircraft = self._sort_by_arrival_time(arrival_aircraft)
        
        # 为每架飞机生成优化指令
        for i, (callsign, state) in enumerate(sorted_aircraft):
            self._generate_commands_for_aircraft(callsign, state, i, len(sorted_aircraft))

    def _sort_by_arrival_time(self, aircraft_dict: Dict) -> List[Tuple[str, Dict]]:
        """按预计到达时间排序"""
        aircraft_with_eta = []
        
        for callsign, state in aircraft_dict.items():
            # 简化的ETA计算
            distance = state['distances']['direct_to_mp']
            ground_speed = state['speeds']['ground_speed']
            eta = distance / ground_speed * 60 if ground_speed > 0 else 999  # 分钟
            
            aircraft_with_eta.append((callsign, state, eta))
        
        # 按ETA排序
        aircraft_with_eta.sort(key=lambda x: x[2])
        
        return [(item[0], item[1]) for item in aircraft_with_eta]

    def _generate_commands_for_aircraft(self, callsign: str, state: Dict, sequence: int, total: int):
        """为单架飞机生成指令"""
        current_alt = state['position']['altitude']
        current_ias = state['speeds']['ias']
        distances = state['distances']
        flexible_status = state['flexible_status']
        
        # 避免过于频繁的指令
        if time.time() - state.get('last_command_time', 0) < 30:  # 30秒间隔
            return
        
        commands = {}
        
        # 1. 高度管理
        target_alt = self._calculate_target_altitude(state, sequence)
        if abs(current_alt - target_alt) > 500:  # 500英尺容差
            # 检查下降率
            distance_to_mp = distances['direct_to_mp']
            max_descent_distance = (current_alt - self.FINAL_ALTITUDE) / self.MAX_DESCENT_RATE * state['speeds']['ground_speed'] / 60
            
            if distance_to_mp >= max_descent_distance:
                commands['altitude'] = target_alt
                # 计算合理的垂直速度
                time_to_mp = distance_to_mp / state['speeds']['ground_speed'] * 60  # 分钟
                required_vs = min((current_alt - target_alt) / time_to_mp, self.MAX_DESCENT_RATE)
                if required_vs > 500:  # 最小下降率
                    commands['vertical_speed'] = -int(required_vs)
        
        # 2. 速度管理
        target_speed = self._calculate_target_speed(current_alt, distances)
        if abs(current_ias - target_speed) > 10:  # 10节容差
            commands['speed'] = target_speed
        
        # 3. 航路管理 - 灵活进近决策
        if flexible_status['can_direct_mp'] and self._should_direct_to_mp(state, sequence):
            commands['waypoints'] = ['MP']
            print(f"🎯 {callsign} 指令直飞MP")
        
        # 4. 发送指令
        if commands:
            success = self.command_manager.combo(callsign, **commands)
            if success:
                self.aircraft_states[callsign]['last_command_time'] = time.time()
                print(f"📡 {callsign} 优化指令: {commands}")

    def _calculate_target_altitude(self, state: Dict, sequence: int) -> int:
        """计算目标高度"""
        current_alt = state['position']['altitude']
        distance_to_mp = state['distances']['direct_to_mp']
        
        # 基于距离的下降剖面
        if distance_to_mp > 50:  # 50海里外，保持高度
            return current_alt
        elif distance_to_mp > 30:  # 30-50海里，开始下降
            return max(10000, self.FINAL_ALTITUDE + int((distance_to_mp - 30) * 400))
        elif distance_to_mp > 15:  # 15-30海里，继续下降
            return max(6000, self.FINAL_ALTITUDE + int((distance_to_mp - 15) * 200))
        else:  # 15海里内，最终进近
            return self.FINAL_ALTITUDE

    def _calculate_target_speed(self, current_alt: int, distances: Dict) -> int:
        """计算目标速度"""
        distance_to_mp = distances['direct_to_mp']
        
        if current_alt > self.SPEED_TRANSITION_ALT:
            # 高空：逐步减速到250
            if distance_to_mp > 40:
                return 280  # 保持初始速度
            else:
                return 250  # 减速到250
        else:
            # 低空：从250减速到180
            if distance_to_mp > 20:
                return 250
            elif distance_to_mp > 10:
                return 220
            else:
                return self.FINAL_SPEED

    def _should_direct_to_mp(self, state: Dict, sequence: int) -> bool:
        """判断是否应该直飞MP"""
        distances = state['distances']
        
        # 简化的决策逻辑
        direct_distance = distances['direct_to_mp']
        route_distance = distances['remaining_route']
        
        # 如果直飞能节省超过10海里，且距离合适
        if route_distance - direct_distance > 10 and 15 < direct_distance < 40:
            return True
        
        # 考虑间隔管理：如果是队列中较晚的飞机，倾向于走弧线拉开间隔
        if sequence > 2 and direct_distance < 25:
            return False
        
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
    # 🔧 使用清洗后的数据
    cleaned_data = data_processor.process_data(data)
    flight_optimizer.process_update(cleaned_data)  # 使用清洗后的数据

if __name__ == '__main__':
    print("🚀 智能飞行管制系统启动中...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
