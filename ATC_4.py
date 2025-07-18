#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from flask_socketio import SocketIO, emit
import json
import time
import math

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ==============================================
# 第一层：ATC指令集 - ATCCommandManager
# ==============================================

class ATCCommandManager:
    """ATC指令管理器 - 统一管理所有指令发送"""
    
    def __init__(self, socketio_instance):
        self.socketio = socketio_instance
        self.is_connected = False
        self.command_history = []
    
    def set_connection_status(self, status):
        """设置连接状态"""
        self.is_connected = status
    
    def _send_command(self, callsign, instructions):
        """基础指令发送函数"""
        if not self.is_connected:
            print(f"❌ 前端未连接，无法发送指令给 {callsign}")
            return False
        
        command = {
            'callsign': callsign,
            'instructions': instructions
        }
        
        try:
            self.socketio.emit('atc_commands', [command])
            
            # 记录指令历史
            self.command_history.append({
                'timestamp': time.time(),
                'callsign': callsign,
                'instructions': instructions
            })
            
            # 只保留最近的500条记录
            if len(self.command_history) > 500:
                self.command_history.pop(0)
            
            print(f"✅ 指令已发送给 {callsign}: {instructions}")
            return True
        except Exception as e:
            print(f"❌ 指令发送失败 {callsign}: {e}")
            return False
    
    def combo(self, callsign, **kwargs):
        """
        通用组合指令方法 - 根据输入参数自动组合指令
        
        支持的参数:
            altitude: 高度 (int/str)
            speed: 速度 (int)
            heading: 航向 (int)
            vertical_speed: 垂直速度 (int)
            waypoints: 航路点列表 (list)
            waypoint: 单个航点 (str)
            direct_to_mp: 直飞MP (bool)
            resume_route: 恢复航路 (bool)
        """
        instructions = {}
        
        # 处理各种参数
        if 'altitude' in kwargs:
            instructions['altitude'] = kwargs['altitude']
        
        if 'speed' in kwargs:
            instructions['speed'] = kwargs['speed']
        
        if 'heading' in kwargs:
            instructions['heading'] = kwargs['heading']
        
        if 'vertical_speed' in kwargs:
            instructions['verticalSpeed'] = kwargs['vertical_speed']
        
        if 'waypoints' in kwargs:
            instructions['customRoute'] = kwargs['waypoints']
        
        if 'waypoint' in kwargs:
            instructions['directTo'] = kwargs['waypoint']
        
        if 'direct_to_mp' in kwargs and kwargs['direct_to_mp']:
            instructions['directToMP'] = True
        
        if 'resume_route' in kwargs and kwargs['resume_route']:
            instructions['resumeRoute'] = True
        
        # 检查是否有有效指令
        if not instructions:
            print(f"❌ 没有有效的指令参数: {kwargs}")
            return False
        
        return self._send_command(callsign, instructions)
    
    def get_command_history(self):
        """获取指令历史"""
        return self.command_history

# ==============================================
# 第二层：飞行数据处理器 - FlightDataProcessor
# ==============================================

class FlightDataProcessor:
    """飞行数据处理器 - 专注数据清洗和格式转换"""
    
    def __init__(self):
        self.cleaned_aircraft_data = []
        self.last_update_time = None
        self.update_count = 0
    
    def process_data(self, data):
        """处理和清洗飞机数据"""
        self.last_update_time = time.time()
        self.update_count += 1
        
        # 提取飞机数据
        raw_aircraft_data = data.get('aircraft', [])
        
        # 清洗数据
        self.cleaned_aircraft_data = self._clean_aircraft_data(raw_aircraft_data)
        
        return self.cleaned_aircraft_data
    
    def _clean_aircraft_data(self, raw_aircraft_data):
        """清洗飞机数据 - 处理数据类型转换"""
        cleaned_data = []
        
        for aircraft in raw_aircraft_data:
            try:
                cleaned_aircraft = self._clean_single_aircraft(aircraft)
                if cleaned_aircraft:
                    cleaned_data.append(cleaned_aircraft)
            except Exception as e:
                print(f"❌ 清洗飞机数据失败: {e}")
                
        return cleaned_data
    
    def _clean_single_aircraft(self, aircraft):
        """清洗单架飞机数据"""
        cleaned = aircraft.copy()
        
        # 清洗位置数据
        if 'position' in cleaned:
            pos = cleaned['position']
            if isinstance(pos, dict):
                # 转换坐标为浮点数
                if 'lat' in pos and pos['lat'] is not None:
                    pos['lat'] = float(pos['lat'])
                if 'lon' in pos and pos['lon'] is not None:
                    pos['lon'] = float(pos['lon'])
                if 'altitude' in pos and pos['altitude'] is not None:
                    pos['altitude'] = int(pos['altitude'])
        
        # 清洗速度数据
        if 'speed' in cleaned:
            speed = cleaned['speed']
            if isinstance(speed, dict):
                if 'ias' in speed and speed['ias'] is not None:
                    speed['ias'] = int(speed['ias'])
                if 'tas' in speed and speed['tas'] is not None:
                    speed['tas'] = int(speed['tas'])
                if 'groundSpeed' in speed and speed['groundSpeed'] is not None:
                    speed['groundSpeed'] = int(speed['groundSpeed'])
        
        # 清洗方向数据
        if 'direction' in cleaned:
            direction = cleaned['direction']
            if isinstance(direction, dict):
                if 'heading' in direction and direction['heading'] is not None:
                    direction['heading'] = int(direction['heading'])
                if 'track' in direction and direction['track'] is not None:
                    direction['track'] = int(direction['track'])
        
        # 清洗垂直数据
        if 'vertical' in cleaned:
            vertical = cleaned['vertical']
            if isinstance(vertical, dict):
                if 'verticalSpeed' in vertical and vertical['verticalSpeed'] is not None:
                    vertical['verticalSpeed'] = int(vertical['verticalSpeed'])
                if 'targetAltitude' in vertical and vertical['targetAltitude'] is not None:
                    vertical['targetAltitude'] = int(vertical['targetAltitude'])
        
        # 清洗灵活进近数据
        if 'flexibleApproach' in cleaned:
            flexible = cleaned['flexibleApproach']
            if isinstance(flexible, dict) and 'distances' in flexible:
                distances = flexible['distances']
                if isinstance(distances, dict):
                    # 去掉nm单位，转换为浮点数
                    for key in ['currentDirectToMP', 'earliestDistanceToMP', 'latestDistanceToMP', 'customRouteRemaining']:
                        if key in distances and distances[key] is not None:
                            value = str(distances[key])
                            # 移除nm单位
                            if 'nm' in value:
                                value = value.replace('nm', '').strip()
                            distances[key] = float(value)
        
        return cleaned
    
    def get_aircraft_data(self):
        """获取清洗后的飞机数据"""
        return self.cleaned_aircraft_data

# ==============================================
# 第三层：飞行优化器 - FlightOptimizer
# ==============================================

class FlightOptimizer:
    """飞行优化器 - 接收清洗后的数据"""
    
    def __init__(self, command_manager):
        self.command_manager = command_manager
        self.optimization_history = []
        self.last_optimization_time = 0
        self.optimization_interval = 15
        self.analysis_count = 0
        
        print("🤖 飞行优化器已启动")
        print(f"   优化间隔: {self.optimization_interval}秒")
    
    def process_update(self, aircraft_data):
        """处理清洗后的飞机数据"""
        self.analysis_count += 1
        
        # 显示收到的数据
        current_time = time.strftime('%H:%M:%S')
        print(f"📡 #{self.analysis_count} - {current_time} - 收到{len(aircraft_data)}架清洗后的飞机数据")
        
        # 显示飞机信息
        if aircraft_data:
            for aircraft in aircraft_data:
                callsign = aircraft.get('callsign', 'Unknown')
                pos = aircraft.get('position', {})
                altitude = pos.get('altitude', 'N/A')
                lat = pos.get('lat', 'N/A')
                lon = pos.get('lon', 'N/A')
                
                print(f"   {callsign}: 高度={altitude}, 位置=({lat}, {lon})")
        
        # 检查是否需要优化
        if not self._should_optimize():
            print("   ⏰ 优化间隔未到，跳过优化")
            return
        
        # 优化分析
        self._analyze_and_optimize(aircraft_data)
        self.last_optimization_time = time.time()
    
    def _should_optimize(self):
        """判断是否需要执行优化"""
        current_time = time.time()
        return (current_time - self.last_optimization_time) >= self.optimization_interval
    
    def _analyze_and_optimize(self, aircraft_data):
        """分析优化 - 现在可以直接进行数值计算"""
        print("   🔍 开始优化分析...")
        
        optimization_actions = []
        
        # 现在可以直接进行数值计算，不会有类型错误！
        for aircraft in aircraft_data:
            callsign = aircraft.get('callsign')
            pos = aircraft.get('position', {})
            
            # 直接使用数值类型
            altitude = pos.get('altitude')
            lat = pos.get('lat')
            lon = pos.get('lon')
            
            if altitude and lat and lon:
                print(f"   📊 {callsign}: 高度={altitude}ft, 坐标=({lat:.2f}, {lon:.2f})")
                
                # 简单优化示例：低于25000ft的飞机爬升到25000ft
                if altitude < 25000:
                    optimization_actions.append({
                        'type': 'altitude_optimization',
                        'callsign': callsign,
                        'altitude': 25000,
                        'reason': f'高度优化 - 从{altitude}ft爬升到25000ft'
                    })
                
                # 检查灵活进近优化
                flexible = aircraft.get('flexibleApproach', {})
                if flexible:
                    distances = flexible.get('distances', {})
                    if distances:
                        current_direct = distances.get('currentDirectToMP')
                        earliest = distances.get('earliestDistanceToMP')
                        
                        # 现在可以直接进行数值比较
                        if current_direct and earliest and current_direct > earliest + 5:
                            optimization_actions.append({
                                'type': 'route_optimization',
                                'callsign': callsign,
                                'reason': f'航路优化 - 可节省{current_direct - earliest:.1f}nm'
                            })
        
        # 执行优化指令
        if optimization_actions:
            self._execute_optimizations(optimization_actions)
        else:
            print("   ✅ 当前状态良好，无需优化")
    
    def _execute_optimizations(self, actions):
        """执行优化指令"""
        print(f"   🚀 执行 {len(actions)} 个优化指令:")
        
        for action in actions:
            success = self._execute_single_action(action)
            
            # 记录优化历史
            self.optimization_history.append({
                'timestamp': time.time(),
                'action': action,
                'success': success
            })
    
    def _execute_single_action(self, action):
        """执行单个优化指令"""
        try:
            action_type = action['type']
            callsign = action['callsign']
            reason = action.get('reason', '优化')
            
            print(f"      📤 {callsign}: {reason}")
            
            if action_type == 'altitude_optimization':
                return self.command_manager.combo(callsign, altitude=action['altitude'])
            
            elif action_type == 'route_optimization':
                return self.command_manager.combo(callsign, direct_to_mp=True)
            
            elif action_type == 'speed_optimization':
                return self.command_manager.combo(callsign, speed=action['speed'])
            
            elif action_type == 'heading_optimization':
                return self.command_manager.combo(callsign, heading=action['heading'])
            
            else:
                print(f"      ❌ 未知的指令类型: {action_type}")
                return False
                
        except Exception as e:
            print(f"      ❌ 执行指令失败: {e}")
            return False

# ==============================================
# 主系统集成
# ==============================================

# 全局实例
command_manager = ATCCommandManager(socketio)
data_processor = FlightDataProcessor()
flight_optimizer = FlightOptimizer(command_manager)  # 🔧 修正：只传一个参数

@socketio.on('connect')
def handle_connect():
    """处理前端连接"""
    command_manager.set_connection_status(True)
    print("✅ 前端已连接")
    emit('connected', {'message': '后端已连接'})

@socketio.on('disconnect')
def handle_disconnect():
    """处理前端断开"""
    command_manager.set_connection_status(False)
    print("❌ 前端已断开")

@socketio.on('aircraft_data')
def handle_aircraft_data(data):
    """处理飞机数据 - 简化版"""
    # 第一步：数据清洗
    cleaned_data = data_processor.process_data(data)
    
    # 第二步：优化分析
    flight_optimizer.process_update(cleaned_data)

# 启动服务器
if __name__ == '__main__':
    print("🚀 智能飞行管制系统启动中...")
    print("📡 三层架构:")
    print("   1. ATCCommandManager - 指令发送")
    print("   2. FlightDataProcessor - 数据清洗")
    print("   3. FlightOptimizer - 优化分析")
    print()
    
    separator = "=" * 60
    print(separator)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
