import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

class FlightPhase(Enum):
    CRUISE = "cruise"
    INITIAL_DESCENT = "initial_descent"
    SPEED_REDUCTION_250 = "speed_reduction_250"
    INTERMEDIATE_DESCENT = "intermediate_descent"
    FINAL_APPROACH_PREP = "final_approach_prep"
    FINAL_APPROACH = "final_approach"
    LANDING = "landing"

@dataclass
class FlightState:
    """飞行状态数据"""
    altitude: float  # 高度 (ft)
    ground_speed: float  # 地速 (kt)
    distance_to_runway: float  # 距离跑道距离 (nm)
    target_altitude: float  # 目标高度 (ft)
    current_descent_rate: float  # 当前下降率 (ft/min)
    flap_setting: int  # 襟翼设置
    airport_elevation: float  # 机场标高 (ft)

class DescentController:
    """下降控制器"""
    
    def __init__(self):
        # 标准下降率映射 (90%情况使用)
        self.standard_descent_rates = {
            500: "slow_descent",      # 慢慢下
            1000: "normal_descent",   # 正常下
            1500: "fast_descent",     # 快点下
            2000: "very_fast_descent" # 超快下
        }
        
        # 关键节点定义
        self.key_points = {
            'faf_distance': 9,        # FAF点距离 (nm)
            'faf_altitude': 900,      # FAF点高度 (ft)
            'flap5_distance': 10,     # 襟翼5点距离 (nm)
            'speed_250_altitude': 10000,  # 250kt减速高度 (ft)
            'final_approach_speed': 180,  # 最终进近速度 (kt)
        }
        
        # 余度设置
        self.safety_margins = {
            'above_20000': 15,  # 20000ft以上余度 (nm)
            'mid_altitude': 8,  # 中高度余度 (nm)
            'below_10000': 3,   # 10000ft以下余度 (nm)
        }
        
        self.current_phase = FlightPhase.CRUISE
        
    def calculate_required_descent_distance(self, altitude_to_lose: float, 
                                          average_ground_speed: float) -> float:
        """计算所需下降距离"""
        # 3度下滑角计算：高度/距离 = tan(3°) ≈ 0.0524
        # 距离 = 高度 / tan(3°) / 6076 (转换为海里)
        glide_slope_distance = altitude_to_lose / math.tan(math.radians(3)) / 6076
        return glide_slope_distance
        
    def get_optimal_descent_rate(self, flight_state: FlightState) -> int:
        """获取最优下降率 (90%情况的标准化选择)"""
        ground_speed = flight_state.ground_speed
        altitude = flight_state.altitude
        distance = flight_state.distance_to_runway
        
        # 计算高距比紧张程度
        altitude_to_lose = altitude - flight_state.target_altitude
        required_distance = self.calculate_required_descent_distance(altitude_to_lose, ground_speed)
        margin_ratio = distance / required_distance if required_distance > 0 else 999
        
        # 90%情况的标准选择逻辑
        if margin_ratio > 1.5:  # 余度充足
            if ground_speed > 350:
                return 1000  # 高速时适中下降
            else:
                return 500   # 慢慢下
        elif margin_ratio > 1.2:  # 余度适中
            return 1000  # 正常下
        elif margin_ratio > 1.0:  # 余度紧张
            return 1500  # 快点下
        else:  # 余度不足
            return 2000  # 超快下
            
    def get_descent_rate_by_ground_speed(self, ground_speed: float) -> int:
        """剩余9%情况：地速的一半"""
        return int(ground_speed / 2)
        
    def calculate_speed_reduction_distance(self, current_speed: float, 
                                         target_speed: float) -> float:
        """计算减速距离"""
        speed_diff = current_speed - target_speed
        # 经验公式：每10kt速度差需要约0.5nm距离
        return speed_diff * 0.5 / 10
        
    def update_flight_phase(self, flight_state: FlightState) -> FlightPhase:
        """更新飞行阶段"""
        altitude = flight_state.altitude
        distance = flight_state.distance_to_runway
        ground_speed = flight_state.ground_speed
        
        if altitude > 15000 and self.current_phase == FlightPhase.CRUISE:
            return FlightPhase.INITIAL_DESCENT
        elif altitude > 10000 and altitude <= 15000:
            return FlightPhase.SPEED_REDUCTION_250
        elif altitude <= 10000 and distance > 15:
            return FlightPhase.INTERMEDIATE_DESCENT
        elif distance <= 15 and distance > 10:
            return FlightPhase.FINAL_APPROACH_PREP
        elif distance <= 10:
            return FlightPhase.FINAL_APPROACH
        else:
            return self.current_phase
            
    def get_safety_margin(self, altitude: float) -> float:
        """获取安全余度"""
        if altitude > 20000:
            return self.safety_margins['above_20000']
        elif altitude > 10000:
            return self.safety_margins['mid_altitude']
        else:
            return self.safety_margins['below_10000']
            
    def calculate_descent_command(self, flight_state: FlightState) -> dict:
        """计算下降指令"""
        # 更新飞行阶段
        self.current_phase = self.update_flight_phase(flight_state)
        
        # 获取安全余度
        safety_margin = self.get_safety_margin(flight_state.altitude)
        
        # 计算实际可用距离
        available_distance = flight_state.distance_to_runway - safety_margin
        
        # 90%情况使用标准下降率
        standard_descent_rate = self.get_optimal_descent_rate(flight_state)
        
        # 9%情况使用地速一半
        speed_based_descent_rate = self.get_descent_rate_by_ground_speed(flight_state.ground_speed)
        
        # 选择最终下降率
        if abs(standard_descent_rate - speed_based_descent_rate) < 200:
            # 两者接近，使用标准下降率
            final_descent_rate = standard_descent_rate
            method = "standard_90_percent"
        else:
            # 使用地速一半的方法
            final_descent_rate = speed_based_descent_rate
            method = "ground_speed_9_percent"
            
        # 特殊情况处理
        commands = {
            'descent_rate': final_descent_rate,
            'target_speed': self._get_target_speed(flight_state),
            'flap_setting': self._get_flap_setting(flight_state),
            'flight_phase': self.current_phase.value,
            'method_used': method,
            'safety_margin': safety_margin,
            'available_distance': available_distance
        }
        
        return commands
        
    def _get_target_speed(self, flight_state: FlightState) -> float:
        """获取目标速度"""
        if flight_state.altitude > 10000:
            return min(flight_state.ground_speed, 300)  # 高空限速
        elif flight_state.distance_to_runway > 10:
            return 250  # 中低空250kt
        else:
            return 180  # 最终进近180kt
            
    def _get_flap_setting(self, flight_state: FlightState) -> int:
        """获取襟翼设置"""
        if flight_state.distance_to_runway <= 10:
            return 5  # 10nm内襟翼5
        elif flight_state.distance_to_runway <= 9:
            return 15  # FAF点襟翼15
        else:
            return 0  # 清洁形态
            
    def monitor_descent_progress(self, flight_state: FlightState) -> dict:
        """监控下降进度"""
        altitude_to_lose = flight_state.altitude - flight_state.target_altitude
        time_remaining = altitude_to_lose / flight_state.current_descent_rate if flight_state.current_descent_rate > 0 else 999
        
        # 检查高距比
        required_distance = self.calculate_required_descent_distance(altitude_to_lose, flight_state.ground_speed)
        margin_ratio = flight_state.distance_to_runway / required_distance if required_distance > 0 else 999
        
        status = {
            'altitude_to_lose': altitude_to_lose,
            'time_remaining_minutes': time_remaining,
            'margin_ratio': margin_ratio,
            'status': 'on_profile' if 0.9 < margin_ratio < 1.3 else 'off_profile'
        }
        
        return status

# 使用示例
def simulate_descent():
    """模拟下降过程"""
    controller = DescentController()
    
    # 初始飞行状态
    flight_state = FlightState(
        altitude=27000,  # 27000ft
        ground_speed=400,  # 400kt
        distance_to_runway=130,  # 130nm
        target_altitude=900,  # 目标高度900ft
        current_descent_rate=0,  # 当前平飞
        flap_setting=0,  # 清洁形态
        airport_elevation=0  # 海平面机场
    )
    
    print("=== 飞行下降控制系统模拟 ===")
    print(f"初始状态: 高度{flight_state.altitude}ft, 地速{flight_state.ground_speed}kt, 距离{flight_state.distance_to_runway}nm")
    print()
    
    # 模拟下降过程
    for i in range(10):
        # 计算控制指令
        commands = controller.calculate_descent_command(flight_state)
        
        # 监控进度
        progress = controller.monitor_descent_progress(flight_state)
        
        print(f"阶段 {i+1}: {commands['flight_phase']}")
        print(f"  下降率: {commands['descent_rate']} ft/min ({commands['method_used']})")
        print(f"  目标速度: {commands['target_speed']} kt")
        print(f"  襟翼设置: {commands['flap_setting']}")
        print(f"  安全余度: {commands['safety_margin']} nm")
        print(f"  高距比状态: {progress['status']}")
        print(f"  剩余时间: {progress['time_remaining_minutes']:.1f} 分钟")
        print()
        
        # 模拟飞行状态更新
        flight_state.altitude -= commands['descent_rate'] * 2  # 假设2分钟间隔
        flight_state.distance_to_runway -= flight_state.ground_speed * 2 / 60  # 2分钟飞行距离
        flight_state.ground_speed = commands['target_speed']
        flight_state.current_descent_rate = commands['descent_rate']
        flight_state.flap_setting = commands['flap_setting']
        
        # 检查是否接近目标
        if flight_state.altitude <= 2000 or flight_state.distance_to_runway <= 5:
            print("接近最终进近阶段，模拟结束")
            break
            
if __name__ == "__main__":
    simulate_descent()
