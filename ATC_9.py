#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from flask_socketio import SocketIO, emit
import time
import math
import numpy as np
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# 导入环境数据
from env_data import waypointData, windData, routes


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
# 新增：数据结构定义
# ==============================================

@dataclass
class TrajectoryPoint:
    """4D轨迹点"""
    time: float
    lat: float
    lon: float
    altitude: float
    speed: float
    distance_to_mp: float

@dataclass
class ConflictInfo:
    """冲突信息"""
    aircraft1: str
    aircraft2: str
    time: float
    distance: float
    altitude_separation: float
    conflict_type: str  # 'horizontal', 'vertical', 'both'

@dataclass
class DescentProfile:
    """下降剖面"""
    start_distance: float
    start_altitude: float
    descent_rate: int
    decel_start_distance: float
    strategy: str  # 'normal', 'early_decel', 'speedbrake'

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
# 第三层：增强版优化器
# ==============================================

class AdvancedFlightOptimizer:
    """增强版飞行优化器 - 4D轨迹优化与冲突解决"""
    
    def __init__(self, command_manager):
        self.command_manager = command_manager
        self.waypoints = waypointData
        self.wind_data = windData
        self.routes = routes
        self.aircraft_states = {}
        self.analysis_count = 0
        
        # 优化参数
        self.FINAL_ALTITUDE = 2000
        self.FINAL_SPEED = 180
        self.MIN_HORIZONTAL_SEP = 3.0  # 海里
        self.MIN_VERTICAL_SEP = 1000   # 英尺
        self.MP_SEPARATION = 5.0       # 海里
        self.MAX_DESCENT_RATE = 2000   # ft/min
        self.PREDICTION_TIME = 600     # 预测10分钟
        self.COMMAND_INTERVAL = 30     # 指令间隔30秒
        
        # 4D轨迹预测参数
        self.TIME_STEP = 30            # 30秒时间步长
        self.DESCENT_RATIO = 3         # 3:1下降比
        
        # 灵活进近区域
        self.flexible_zones = {
            'A Arrival': {'start': 'IR15', 'end': 'IL17'},
            'B Arrival': {'start': 'IR15', 'end': 'IL17'},
            'C Arrival': {'start': 'L3', 'end': 'R21'},
            'D Arrival': {'start': 'L17', 'end': 'R21'}
        }
        
        print("🚀 增强版飞行优化器初始化完成")
        print(f"🧠 核心功能: 4D轨迹预测 + 智能冲突解决 + 多机协调优化")

    def process_update(self, flight_data):
        """主处理函数"""
        self.analysis_count += 1
        current_time = time.strftime('%H:%M:%S')
        
        print(f"\n🎯 #{self.analysis_count} - 系统: {current_time} | 模拟: {flight_data['sim_time']}")
        print("=" * 80)
        
        # 分析所有进港飞机
        arrival_aircraft = self._analyze_all_aircraft(flight_data['aircraft_list'])
        
        if not arrival_aircraft:
            print("⏸️ 无进港飞机")
            return
        
        # 核心优化流程a
        conflicts = self._detect_conflicts(arrival_aircraft)
        optimized_solution = self._multi_aircraft_optimization(arrival_aircraft, conflicts)
        self._execute_commands(optimized_solution)
        
        print("✅ 优化完成\n")

    def _analyze_all_aircraft(self, aircraft_list):
        """分析所有进港飞机"""
        arrival_aircraft = []
        
        for aircraft in aircraft_list:
            if self._is_arrival_aircraft(aircraft):
                state = self._analyze_single_aircraft(aircraft)
                self.aircraft_states[state['callsign']] = state
                arrival_aircraft.append(state)
        
        if arrival_aircraft:
            self._display_aircraft_status(arrival_aircraft)
        
        return arrival_aircraft

    def _is_arrival_aircraft(self, aircraft):
        """判断是否为进港飞机"""
        flight_type = aircraft['flight_type']
        route_name = aircraft['route_name']
        return flight_type == 'ARRIVAL' or 'Arrival' in route_name

    def _analyze_single_aircraft(self, aircraft):
        """分析单架飞机的完整状态"""
        callsign = aircraft['callsign']
        lat = float(aircraft['lat'])
        lon = float(aircraft['lon'])
        altitude = int(aircraft['altitude'])
        ias = int(aircraft['ias'])
        heading = int(aircraft['heading'])
        vertical_speed = int(aircraft['vertical_speed'])
        route_name = aircraft['route_name']
        aircraft_type = aircraft['aircraft_type']
        
        # 计算风影响和地速
        wind_info = get_wind_at_altitude(altitude, self.wind_data)
        tas = ias_to_tas(ias, altitude, wind_info['temp'])
        gs_info = calculate_ground_speed_and_track(tas, heading, wind_info['direction'], wind_info['speed'])
        
        # 计算到MP距离
        mp_pos = self.waypoints.get('MP', {'lat': 0, 'lon': 0})
        distance_to_mp = calculate_distance(lat, lon, mp_pos['lat'], mp_pos['lon'])
        
        # 计算ETA范围
        eta_info = self._calculate_eta_range(lat, lon, route_name, gs_info['speed'], distance_to_mp)
        
        # 生成4D轨迹预测
        trajectory = self._predict_4d_trajectory(lat, lon, altitude, ias, heading, route_name)
        
        # 计算最优下降剖面
        descent_profile = self._calculate_optimal_descent_profile(altitude, distance_to_mp, gs_info['speed'])
        
        state = {
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
            'eta_info': eta_info,
            'trajectory': trajectory,
            'descent_profile': descent_profile,
            'last_command_time': self.aircraft_states.get(callsign, {}).get('last_command_time', 0),
            'priority': self._calculate_priority(callsign, distance_to_mp, eta_info['earliest_eta'])
        }
        
        return state

    def _predict_4d_trajectory(self, lat, lon, altitude, ias, heading, route_name):
        """预测4D轨迹"""
        trajectory = []
        current_lat, current_lon = lat, lon
        current_alt = altitude
        current_ias = ias
        current_time = 0
        
        mp_pos = self.waypoints.get('MP', {'lat': 0, 'lon': 0})
        
        # 简化轨迹预测：假设当前状态继续
        for i in range(int(self.PREDICTION_TIME / self.TIME_STEP)):
            # 计算当前状态下的地速
            wind_info = get_wind_at_altitude(current_alt, self.wind_data)
            tas = ias_to_tas(current_ias, current_alt, wind_info['temp'])
            gs_info = calculate_ground_speed_and_track(tas, heading, wind_info['direction'], wind_info['speed'])
            
            # 计算距离MP的距离
            distance_to_mp = calculate_distance(current_lat, current_lon, mp_pos['lat'], mp_pos['lon'])
            澳洲机队
            if distance_to_mp < 1:  # 到达MP
                break
            
            # 预测下一个位置（简化为直线飞行）
            distance_step = gs_info['speed'] * (self.TIME_STEP / 3600)  # 海里
            if distance_step < distance_to_mp:
                # 计算新位置
                bearing = math.atan2(mp_pos['lon'] - current_lon, mp_pos['lat'] - current_lat)
                lat_step = distance_step * math.cos(bearing) / 60  # 纬度度数
                lon_step = distance_step * math.sin(bearing) / (60 * math.cos(math.radians(current_lat)))
                
                current_lat += lat_step
                current_lon += lon_step
            
            trajectory.append(TrajectoryPoint(
                time=current_time,
                lat=current_lat,
                lon=current_lon,
                altitude=current_alt,
                speed=current_ias,
                distance_to_mp=distance_to_mp
            ))
            
            current_time += self.TIME_STEP
        
        return trajectory

    def _calculate_eta_range(self, lat, lon, route_name, ground_speed, distance_to_mp):
        """计算ETA范围"""
        if route_name not in self.flexible_zones:
            # 固定航线
            eta = distance_to_mp / ground_speed * 60 if ground_speed > 0 else 999
            return {'earliest_eta': eta, 'latest_eta': eta, 'time_window': 0}
        
        # 灵活进近航线
        zone = self.flexible_zones[route_name]
        start_point = zone['start']
        end_point = zone['end']
        
        # 最早ETA：直飞MP
        earliest_eta = distance_to_mp / ground_speed * 60 if ground_speed > 0 else 999
        
        # 最晚ETA：完整弧线（估算增加30%距离）
        latest_distance = distance_to_mp * 1.3
        latest_eta = latest_distance / ground_speed * 60 if ground_speed > 0 else 999
        
        return {
            'earliest_eta': earliest_eta,
            'latest_eta': latest_eta,
            'time_window': latest_eta - earliest_eta
        }

    def _calculate_optimal_descent_profile(self, current_altitude, distance_to_mp, ground_speed):
        """计算最优下降剖面"""
        altitude_to_lose = current_altitude - self.FINAL_ALTITUDE
        distance_needed = altitude_to_lose / self.DESCENT_RATIO  # 3:1比例
        
        # 估算减速距离
        speed_reduction_needed = 120  # 假设从300kt减到180kt
        decel_distance = speed_reduction_needed / 10  # 简化：每10kt需要1nm
        
        # 选择策略
        if distance_to_mp > distance_needed + decel_distance + 20:
            strategy = 'normal'
            start_distance = distance_needed + 10
            descent_rate = 1000
        elif distance_to_mp > distance_needed + 10:
            strategy = 'early_decel'
            start_distance = distance_to_mp - 10
            descent_rate = 1500
        else:
            strategy = 'speedbrake'
            start_distance = distance_to_mp - 5
            descent_rate = 2000
        
        return DescentProfile(
            start_distance=start_distance,
            start_altitude=current_altitude,
            descent_rate=descent_rate,
            decel_start_distance=distance_needed + decel_distance,
            strategy=strategy
        )

    def _calculate_priority(self, callsign, distance_to_mp, earliest_eta):
        """计算飞机优先级"""
        # 简化优先级计算：距离近的优先级高
        base_priority = 1000 - distance_to_mp
        
        # 可以根据航班类型、延误情况等调整
        if 'CCA' in callsign:  # 示例：国航优先级稍高
            base_priority += 10
        
        return base_priority

    def _detect_conflicts(self, aircraft_list):
        """冲突检测"""
        conflicts = []
        
        for i, aircraft1 in enumerate(aircraft_list):
            for j, aircraft2 in enumerate(aircraft_list[i+1:], i+1):
                conflict = self._check_pair_conflict(aircraft1, aircraft2)
                if conflict:
                    conflicts.append(conflict)
        
        if conflicts:
            print(f"⚠️ 检测到 {len(conflicts)} 个潜在冲突:")
            for conflict in conflicts:
                print(f"   {conflict.aircraft1} vs {conflict.aircraft2}: "
                      f"{conflict.conflict_type} 冲突在 {conflict.time:.1f}min")
        
        return conflicts

    def _check_pair_conflict(self, aircraft1, aircraft2):
        """检查两架飞机的冲突"""
        traj1 = aircraft1['trajectory']
        traj2 = aircraft2['trajectory']
        
        # 简化冲突检测：检查轨迹点的最小距离
        min_distance = float('inf')
        conflict_time = 0
        min_alt_sep = float('inf')
        
        min_len = min(len(traj1), len(traj2))
        
        for i in range(min_len):
            point1 = traj1[i]
            point2 = traj2[i]
            
            # 计算水平距离
            horizontal_dist = calculate_distance(point1.lat, point1.lon, point2.lat, point2.lon)
            
            # 计算垂直间隔
            vertical_sep = abs(point1.altitude - point2.altitude)
            
            if horizontal_dist < min_distance:
                min_distance = horizontal_dist
                conflict_time = point1.time / 60  # 转换为分钟
                min_alt_sep = vertical_sep
        
        # 判断是否冲突
        horizontal_conflict = min_distance < self.MIN_HORIZONTAL_SEP
        vertical_conflict = min_alt_sep < self.MIN_VERTICAL_SEP
        
        if horizontal_conflict and vertical_conflict:
            return ConflictInfo(
                aircraft1=aircraft1['callsign'],
                aircraft2=aircraft2['callsign'],
                time=conflict_time,
                distance=min_distance,
                altitude_separation=min_alt_sep,
                conflict_type='both'
            )
        elif horizontal_conflict:
            return ConflictInfo(
                aircraft1=aircraft1['callsign'],
                aircraft2=aircraft2['callsign'],
                time=conflict_time,
                distance=min_distance,
                altitude_separation=min_alt_sep,
                conflict_type='horizontal'
            )
        
        return None

    def _multi_aircraft_optimization(self, aircraft_list, conflicts):
        """多机协调优化"""
        print(f"\n🧠 多机协调优化: {len(aircraft_list)} 架飞机, {len(conflicts)} 个冲突")
        
        # 按优先级排序
        sorted_aircraft = sorted(aircraft_list, key=lambda x: x['priority'], reverse=True)
        
        # 生成优化方案
        solution = {}
        
        for aircraft in sorted_aircraft:
            callsign = aircraft['callsign']
            
            # 为每架飞机生成优化指令
            commands = self._generate_optimization_commands(aircraft, conflicts, solution)
            solution[callsign] = commands
            
            if commands:
                print(f"  🎯 {callsign}: {commands}")
        
        return solution

    def _generate_optimization_commands(self, aircraft, conflicts, existing_solution):
        """为单机生成优化指令"""
        callsign = aircraft['callsign']
        current_alt = aircraft['altitude']
        current_ias = aircraft['ias']
        distance_to_mp = aircraft['distance_to_mp']
        descent_profile = aircraft['descent_profile']
        
        # 检查指令冷却
        time_since_last = time.time() - aircraft.get('last_command_time', 0)
        if time_since_last < self.COMMAND_INTERVAL:
            return {}
        
        commands = {}
        
        # 高度管理：基于下降剖面
        if distance_to_mp <= descent_profile.start_distance and current_alt > self.FINAL_ALTITUDE:
            target_altitude = self._calculate_target_altitude(aircraft)
            if abs(current_alt - target_altitude) > 500:
                commands['altitude'] = target_altitude
                commands['vertical_speed'] = -descent_profile.descent_rate
        
        # 速度管理：基于距离和冲突
        target_speed = self._calculate_target_speed(aircraft, conflicts)
        if abs(current_ias - target_speed) > 10:
            commands['speed'] = target_speed
        
        # 航路管理：冲突解决
        if self._should_use_arc_route(aircraft, conflicts):
            commands['waypoints'] = self._generate_arc_waypoints(aircraft)
        
        return commands

    def _calculate_target_altitude(self, aircraft):
        """计算目标高度"""
        distance_to_mp = aircraft['distance_to_mp']
        current_alt = aircraft['altitude']
        
        if distance_to_mp > 50:
            return min(current_alt, 15000)
        elif distance_to_mp > 30:
            return min(current_alt, 10000)
        elif distance_to_mp > 15:
            return min(current_alt, 6000)
        else:
            return self.FINAL_ALTITUDE

    def _calculate_target_speed(self, aircraft, conflicts):
        """计算目标速度"""
        distance_to_mp = aircraft['distance_to_mp']
        current_alt = aircraft['altitude']
        
        # 检查是否涉及冲突
        involved_in_conflict = any(
            aircraft['callsign'] in [c.aircraft1, c.aircraft2] 
            for c in conflicts
        )
        
        if current_alt > 10000:
            base_speed = 250
        elif distance_to_mp > 20:
            base_speed = 250
        elif distance_to_mp > 10:
            base_speed = 220
        else:
            base_speed = self.FINAL_SPEED
        
        # 如果有冲突，可能需要调整速度
        if involved_in_conflict:
            base_speed = max(base_speed - 20, self.FINAL_SPEED)
        
        return base_speed

    def _should_use_arc_route(self, aircraft, conflicts):
        """判断是否应该使用弧线航路"""
        callsign = aircraft['callsign']
        route_name = aircraft['route_name']
        
        # 只有灵活航线才能选择
        if route_name not in self.flexible_zones:
            return False
        
        # 检查是否有冲突需要解决
        has_conflict = any(
            callsign in [c.aircraft1, c.aircraft2] 
            for c in conflicts
        )
        
        # 如果有冲突且时间窗口足够，使用弧线延误
        if has_conflict and aircraft['eta_info']['time_window'] > 10:
            return True
        
        return False

    def _generate_arc_waypoints(self, aircraft):
        """生成弧线航路点"""
        route_name = aircraft['route_name']
        zone = self.flexible_zones.get(route_name, {})
        
        waypoints = []
        if 'end' in zone and zone['end'] in self.waypoints:
            end_point = self.waypoints[zone['end']]
            waypoints.append([end_point['lat'], end_point['lon']])
        
        # 添加MP
        if 'MP' in self.waypoints:
            mp = self.waypoints['MP']
            waypoints.append([mp['lat'], mp['lon']])
        
        return waypoints

    def _execute_commands(self, solution):
        """执行优化指令"""
        command_count = 0
        
        for callsign, commands in solution.items():
            if commands and self.command_manager.combo(callsign, **commands):
                # 更新最后指令时间
                if callsign in self.aircraft_states:
                    self.aircraft_states[callsign]['last_command_time'] = time.time()
                command_count += 1
        
        print(f"📡 执行了 {command_count} 条优化指令")

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
            eta_info = state['eta_info']
            descent_profile = state['descent_profile']
            
            print(f"  ✈️ {callsign} ({aircraft_type}) - {route_name}")
            print(f"     位置: ({lat:.3f}, {lon:.3f}) {altitude}ft | IAS: {ias}kt | VS: {vertical_speed:+d}fpm")
            print(f"     地速: {ground_speed:.0f}kt | 距MP: {distance_to_mp:.1f}nm | 优先级: {state['priority']:.0f}")
            print(f"     下降策略: {descent_profile.strategy} | 开始距离: {descent_profile.start_distance:.1f}nm")
            
            if eta_info['time_window'] > 0:
                print(f"     ETA: {eta_info['earliest_eta']:.1f}~{eta_info['latest_eta']:.1f}min (窗口: {eta_info['time_window']:.1f}min)")
            else:
                print(f"     ETA: {eta_info['earliest_eta']:.1f}min (固定)")

# ==============================================
# 主系统
# ==============================================

command_manager = ATCCommandManager(socketio)
data_processor = FlightDataProcessor()
flight_optimizer = AdvancedFlightOptimizer(command_manager)

@socketio.on('connect')
def handle_connect():
    command_manager.set_connection_status(True)
    print("✅ 前端已连接")
    emit('connected', {'message': '增强版后端已连接'})

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
    print("🚀 增强版智能飞行管制系统启动中...")
    print("🧠 核心功能: 4D轨迹预测 + 智能冲突解决 + 多机协调优化")
    print("🎯 优化目标: 安全间隔保证 + 延误最小化 + 距离最优化")
    print("⚡ 实时性能: 30秒决策周期 + 10分钟轨迹预测")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
