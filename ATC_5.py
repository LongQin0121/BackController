#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from flask_socketio import SocketIO, emit
import time
import math

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

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
        """发送指令"""
        if not self.is_connected:
            print(f"❌ 前端未连接，无法发送指令给 {callsign}")
            return False
        
        instructions = {}
        
        if 'altitude' in kwargs:
            instructions['altitude'] = kwargs['altitude']
        if 'speed' in kwargs:
            instructions['speed'] = kwargs['speed']
        if 'heading' in kwargs:
            instructions['heading'] = kwargs['heading']
        if 'direct_to_mp' in kwargs and kwargs['direct_to_mp']:
            instructions['directToMP'] = True
        
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
        """清洗单架飞机数据"""
        cleaned = aircraft.copy()
        
        # 清洗位置数据
        if 'position' in cleaned:
            pos = cleaned['position']
            if isinstance(pos, dict):
                # 🔧 强制转换坐标为浮点数
                if 'lat' in pos and pos['lat'] is not None:
                    try:
                        pos['lat'] = float(pos['lat'])
                        print(f"🔧 清洗纬度: {pos['lat']} -> {type(pos['lat'])}")
                    except (ValueError, TypeError):
                        print(f"❌ 纬度转换失败: {pos['lat']}")
                
                if 'lon' in pos and pos['lon'] is not None:
                    try:
                        pos['lon'] = float(pos['lon'])
                        print(f"🔧 清洗经度: {pos['lon']} -> {type(pos['lon'])}")
                    except (ValueError, TypeError):
                        print(f"❌ 经度转换失败: {pos['lon']}")
                
                if 'altitude' in pos and pos['altitude'] is not None:
                    try:
                        pos['altitude'] = int(pos['altitude'])
                    except (ValueError, TypeError):
                        print(f"❌ 高度转换失败: {pos['altitude']}")
        
        # 清洗灵活进近数据
        if 'flexibleApproach' in cleaned:
            flexible = cleaned['flexibleApproach']
            if isinstance(flexible, dict) and 'distances' in flexible:
                distances = flexible['distances']
                if isinstance(distances, dict):
                    for key in ['currentDirectToMP', 'earliestDistanceToMP']:
                        if key in distances and distances[key] is not None:
                            try:
                                value = str(distances[key]).replace('nm', '').strip()
                                distances[key] = float(value)
                            except (ValueError, TypeError):
                                print(f"❌ 距离转换失败: {distances[key]}")
        
        return cleaned

# ==============================================
# 第三层：优化器
# ==============================================

class FlightOptimizer:
    """飞行优化器 - 使用坐标计算版"""
    
    def __init__(self, command_manager):
        self.command_manager = command_manager
        self.last_optimization_time = 0
        self.optimization_interval = 15
        self.analysis_count = 0
        self.active_commands = {}
        
        print("🤖 飞行优化器已启动")
    
    def process_update(self, aircraft_data):
        """处理清洗后的数据"""
        self.analysis_count += 1
        current_time = time.strftime('%H:%M:%S')
        
        print(f"📡 #{self.analysis_count} - {current_time} - {len(aircraft_data)}架飞机")
        
        # 显示飞机信息和数据类型
        for aircraft in aircraft_data:
            callsign = aircraft.get('callsign', 'Unknown')
            pos = aircraft.get('position', {})
            altitude = pos.get('altitude', 'N/A')
            lat = pos.get('lat', 'N/A')
            lon = pos.get('lon', 'N/A')
            
            print(f"   {callsign}: 高度={altitude}, 位置=({lat}, {lon})")
            print(f"      数据类型: lat={type(lat)}, lon={type(lon)}, alt={type(altitude)}")
        
        # 检查优化间隔
        current_time_stamp = time.time()
        if (current_time_stamp - self.last_optimization_time) < self.optimization_interval:
            print("   ⏰ 优化间隔未到，跳过优化")
            return
        
        # 开始优化
        print("   🔍 开始优化...")
        self._optimize_with_coordinates(aircraft_data)
        self.last_optimization_time = current_time_stamp
    
    def _optimize_with_coordinates(self, aircraft_data):
        """使用坐标计算进行优化"""
        target_lat = 30.0
        target_lon = 115.0
        
        for aircraft in aircraft_data:
            callsign = aircraft.get('callsign')
            pos = aircraft.get('position', {})
            
            try:
                current_lat = pos.get('lat')
                current_lon = pos.get('lon')
                current_altitude = pos.get('altitude')
                
                # 🔧 验证数据类型
                if not isinstance(current_lat, (int, float)):
                    print(f"   ❌ {callsign}: 纬度不是数值类型: {type(current_lat)}")
                    continue
                
                if not isinstance(current_lon, (int, float)):
                    print(f"   ❌ {callsign}: 经度不是数值类型: {type(current_lon)}")
                    continue
                
                print(f"   📍 {callsign}: 当前位置=({current_lat}, {current_lon})")
                
                # 计算距离
                distance = self._calculate_distance(current_lat, current_lon, target_lat, target_lon)
                print(f"   📏 {callsign}: 距离目标={distance:.1f}nm")
                
                # 根据距离优化高度
                if distance > 100:
                    optimal_altitude = 25000
                elif distance > 50:
                    optimal_altitude = 15000
                else:
                    optimal_altitude = 5000
                
                if current_altitude and abs(current_altitude - optimal_altitude) > 1000:
                    print(f"      📤 {callsign}: 高度优化 → {optimal_altitude}ft")
                    self.command_manager.combo(callsign, altitude=optimal_altitude)
                
            except Exception as e:
                print(f"   ❌ {callsign}: 计算失败 - {e}")
    
    def _calculate_distance(self, lat1, lon1, lat2, lon2):
        """计算两点间距离（海里）"""
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return c * 3440.065

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
    # 🔧 修正：使用清洗后的数据
    cleaned_data = data_processor.process_data(data)
    flight_optimizer.process_update(cleaned_data)  # 使用清洗后的数据

if __name__ == '__main__':
    print("🚀 智能飞行管制系统启动中...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
