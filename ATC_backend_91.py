#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Complete Test Backend - Enhanced Custom Route Display with Auto CSN202 Commands
Filename: enhanced_backend.py
🔧 Updated: Now automatically sends commands to CSN202 at 70nm from MP
"""

from flask import Flask
from flask_socketio import SocketIO, emit
import json
import threading
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
full_data = {}  # Store complete data package including time info
aircraft_data = []  # Store aircraft array
is_connected = False

# 🆕 Auto command tracking for CSN202
csn202_auto_commands_sent = False  # Flag to prevent duplicate commands
csn202_target_distance = 70.0  # Target distance in nautical miles

@socketio.on('connect')
def handle_connect():
    global is_connected, csn202_auto_commands_sent
    is_connected = True
    csn202_auto_commands_sent = False  # Reset flag on new connection
    print(f"✅ Frontend connected")
    emit('connected', {'message': 'Enhanced backend connected with auto CSN202 commands'})
    
    # Send CSN202 heading command immediately after connection
    # time.sleep(3)  # Wait 3 seconds for frontend to be fully ready
    # send_csn202_heading_120()

@socketio.on('disconnect')  
def handle_disconnect():
    global is_connected
    is_connected = False
    print(f"❌ Frontend disconnected")

def extract_turning_point_info(aircraft):
    """🆕 Enhanced function to extract turning point information from multiple sources"""
    callsign = aircraft.get('callsign', 'Unknown')
    nav_data = aircraft.get('navigation', {})
    flexible_data = aircraft.get('flexibleApproach', {})
    
    cut_out_point = None
    cut_out_distance = None
    custom_route_info = None
    
    # 🆕 Priority 1: Use navigation.customRoute data (NEW!)
    if isinstance(nav_data, dict):
        custom_route_nav = nav_data.get('customRoute', None)
        if custom_route_nav and isinstance(custom_route_nav, dict):
            turning_point = custom_route_nav.get('turningPoint', None)
            waypoints = custom_route_nav.get('waypoints', [])
            remaining_distance = custom_route_nav.get('remainingDistance', None)
            distance_to_turning_point = custom_route_nav.get('distanceToTurningPoint', None)
            current_index = custom_route_nav.get('currentIndex', 0)
            progress = custom_route_nav.get('progress', 'N/A')
            
            if turning_point:
                cut_out_point = turning_point
                cut_out_distance = distance_to_turning_point
            
            custom_route_info = {
                'waypoints': waypoints,
                'turningPoint': turning_point,
                'remainingDistance': remaining_distance,
                'distanceToTurningPoint': distance_to_turning_point,
                'currentIndex': current_index,
                'progress': progress,
                'waypointCount': len(waypoints) if waypoints else 0
            }
            
            # 🔧 Debug output for CSN202
            if callsign == 'CSN202':
                print(f"🔍 {callsign} 从navigation.customRoute获取:")
                print(f"   waypoints: {waypoints}")
                print(f"   turningPoint: {turning_point}")
                print(f"   remainingDistance: {remaining_distance}")
                print(f"   distanceToTurningPoint: {distance_to_turning_point}")
                print(f"   progress: {progress}")
    
    # Priority 2: Use flexibleApproach.flexibility.cutOutPoint (backup)
    if not cut_out_point and isinstance(flexible_data, dict):
        flexibility = flexible_data.get('flexibility', {})
        if isinstance(flexibility, dict):
            cut_out_point = flexibility.get('cutOutPoint', None)
    
    # Priority 3: Use flexibleApproach.cutOutTiming.cutOutPoint (backup)
    if not cut_out_point and isinstance(flexible_data, dict):
        cut_out_timing = flexible_data.get('cutOutTiming', {})
        if isinstance(cut_out_timing, dict):
            cut_out_point = cut_out_timing.get('cutOutPoint', None)
    
    # Priority 4: Use nextWaypoint as last resort (but exclude MP and invalid values)
    if not cut_out_point and isinstance(nav_data, dict):
        next_wp = nav_data.get('nextWaypoint', 'N/A')
        if next_wp and next_wp not in ['N/A', 'MP', 'END', 'DIRECT', 'HEADING']:
            cut_out_point = next_wp
    
    # 🔧 Get cut out distance from flexible data if not from custom route
    if cut_out_point and not cut_out_distance and isinstance(flexible_data, dict):
        distances = flexible_data.get('distances', {})
        if isinstance(distances, dict):
            cut_out_distance = distances.get('cutOutPointDirect', None)
    
    return cut_out_point, cut_out_distance, custom_route_info

def calculate_descent_rate(ground_speed):
    """Calculate descent rate as 5 times ground speed"""
    try:
        if isinstance(ground_speed, (int, float)) and ground_speed > 0:
            return int(ground_speed * 5)
        else:
            return 1500  # Default descent rate
    except:
        return 1500  # Default descent rate

def send_csn202_auto_descent_and_route(ground_speed):
    """🆕 Send auto descent and custom route commands to CSN202"""
    global csn202_auto_commands_sent
    
    if not is_connected or csn202_auto_commands_sent:
        return False
    
    try:
        # Calculate descent rate (5 times ground speed)
        descent_rate = calculate_descent_rate(ground_speed)
        
        # Send descent command to FL020 with calculated descent rate
        descent_command = {
            'callsign': 'CSN202',
            'instructions': {
                'altitude': 'FL020',
                'verticalSpeed': -descent_rate  # Negative for descent
            }
        }
        
        # Send custom route: current route until IR15, then after IR5 direct to MP
        # Note: This assumes the current route has IR15 and IR5 waypoints
        custom_route_waypoints = ['IR15', 'IR5', 'MP']  # Simplified route
        
        route_command = {
            'callsign': 'CSN202',
            'instructions': {
                'customRoute': custom_route_waypoints
            }
        }
        
        # Send both commands
        socketio.emit('atc_commands', [descent_command])
        time.sleep(0.5)  # Small delay between commands
        socketio.emit('atc_commands', [route_command])
        
        # Mark as sent to prevent duplicate commands
        csn202_auto_commands_sent = True
        
        print(f"🎯 AUTO COMMANDS SENT TO CSN202:")
        print(f"   ⬇️  Descent: FL020 at {descent_rate} fpm (GS: {ground_speed} knots)")
        print(f"   🛣️  Custom Route: {' → '.join(custom_route_waypoints)}")
        print(f"   📍 Triggered at 70nm from MP")
        
        return True
        
    except Exception as e:
        print(f"❌ Error sending auto commands to CSN202: {e}")
        return False

def check_csn202_auto_commands(aircraft):
    """🆕 Check if CSN202 meets conditions for auto commands"""
    global csn202_auto_commands_sent, csn202_target_distance
    
    callsign = aircraft.get('callsign', '')
    if callsign != 'CSN202' or csn202_auto_commands_sent:
        return
    
    # Get flexible approach data
    flexible_data = aircraft.get('flexibleApproach', {})
    if not isinstance(flexible_data, dict):
        return
    
    distances = flexible_data.get('distances', {})
    if not isinstance(distances, dict):
        return
    
    # Check earliest distance to MP
    earliest_distance = distances.get('earliestDistanceToMP', None)
    if earliest_distance is None or earliest_distance == 'N/A':
        return
    
    try:
        earliest_distance_float = float(earliest_distance)
        
        # Check if we've reached the target distance (70nm with some tolerance)
        if earliest_distance_float <= csn202_target_distance and earliest_distance_float > (csn202_target_distance - 5):
            # Get ground speed for descent rate calculation
            speed_data = aircraft.get('speed', {})
            ground_speed = 300  # Default
            
            if isinstance(speed_data, dict):
                gs = speed_data.get('groundSpeed', None)
                if gs and gs != 'N/A':
                    try:
                        ground_speed = float(gs)
                    except:
                        ground_speed = 300
            
            print(f"🚨 CSN202 AUTO COMMAND TRIGGER:")
            print(f"   📏 Earliest Distance to MP: {earliest_distance_float}nm")
            print(f"   🎯 Target Distance: {csn202_target_distance}nm")
            print(f"   💨 Ground Speed: {ground_speed} knots")
            
            # Send the auto commands
            send_csn202_auto_descent_and_route(ground_speed)
            
    except (ValueError, TypeError) as e:
        print(f"⚠️  Error parsing CSN202 distance: {e}")

@socketio.on('aircraft_data')
def handle_aircraft_data(data):
    global aircraft_data, full_data
    
    # Debug: Check data type and content
    print(f"🔍 Received data type: {type(data)}")
    
    # If data is string, parse it as JSON
    if isinstance(data, str):
        try:
            data = json.loads(data)
            print("✅ Successfully parsed JSON string")
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON: {e}")
            return
    
    # Ensure data is a dictionary
    if not isinstance(data, dict):
        print(f"❌ Expected dict, got {type(data)}")
        return
    
    # Store complete data package
    full_data = data
    
    # Extract aircraft array from the data package
    aircraft_data = data.get('aircraft', [])
    
    # Safety check for aircraft_data
    if not isinstance(aircraft_data, list):
        print(f"❌ Expected aircraft list, got {type(aircraft_data)}")
        aircraft_data = []
    
    # Display simulation time info
    sim_time_formatted = data.get('simulationTimeFormatted', 'N/A')
    sim_time = data.get('simulationTime', 0)
    is_running = data.get('isRunning', False)
    sim_speed = data.get('simulationSpeed', 1)
    aircraft_count = data.get('aircraftCount', len(aircraft_data))
    
    print(f"\n📡 Received data - {time.strftime('%H:%M:%S')}")
    print(f"🕐 Simulation Time: {sim_time_formatted} ({sim_time}s)")
    print(f"▶️  Running: {is_running} | Speed: {sim_speed}x | Aircraft: {aircraft_count}")
    
    # Safety check: ensure aircraft_data is valid
    if not aircraft_data:
        print("⚠️  No aircraft data in received package")
        print("-" * 80)
        return
    
    # 🆕 Check for CSN202 auto commands FIRST
    for aircraft in aircraft_data:
        if aircraft and isinstance(aircraft, dict):
            check_csn202_auto_commands(aircraft)
    
    # Print all aircraft status with enhanced flexible approach data
    for i, aircraft in enumerate(aircraft_data):
        if aircraft:
            try:
                callsign = aircraft.get('callsign', f'Aircraft_{i}')
                
                # Position data
                pos = aircraft.get('position', {})
                altitude = pos.get('altitude', 'N/A')
                
                # Speed data
                speed_data = aircraft.get('speed', {})
                if isinstance(speed_data, dict):
                    ias = speed_data.get('ias', 'N/A')
                    tas = speed_data.get('tas', 'N/A')
                    ground_speed = speed_data.get('groundSpeed', 'N/A')
                    speed_str = f"IAS:{ias} TAS:{tas} GS:{ground_speed}"
                else:
                    speed_str = str(speed_data)
                
                # Direction data
                direction_data = aircraft.get('direction', {})
                if isinstance(direction_data, dict):
                    heading = direction_data.get('heading', 'N/A')
                    track = direction_data.get('track', 'N/A')
                    direction_str = f"HDG:{heading} TRK:{track}"
                else:
                    direction_str = str(direction_data)
                
                # Vertical data
                vertical_data = aircraft.get('vertical', {})
                vertical_speed = vertical_data.get('verticalSpeed', 0) if isinstance(vertical_data, dict) else 'N/A'
                vs_str = f" VS:{vertical_speed}" if vertical_speed != 0 else ""
                
                # Navigation data
                nav_data = aircraft.get('navigation', {})
                if isinstance(nav_data, dict):
                    mode = nav_data.get('mode', 'N/A')
                    next_waypoint = nav_data.get('nextWaypoint', 'N/A')
                    climb_phase = nav_data.get('climbPhase', None)
                    planned_route = nav_data.get('plannedRoute', 'N/A')
                else:
                    mode = 'N/A'
                    next_waypoint = 'N/A'
                    climb_phase = None
                    planned_route = 'N/A'
                
                # Aircraft type and flight type
                aircraft_type = aircraft.get('aircraftType', 'UNK')
                flight_type = aircraft.get('type', 'N/A')
                
                # 🆕 Enhanced Flexible Approach Data processing
                flexible_str = ""
                flexible_data = aircraft.get('flexibleApproach', None)
                if flexible_data and isinstance(flexible_data, dict):
                    distances = flexible_data.get('distances', {})
                    flexibility = flexible_data.get('flexibility', {})
                    
                    if isinstance(distances, dict):
                        # 3 key distances
                        current_direct = distances.get('currentDirectToMP', 'N/A')           # Current position direct to MP
                        earliest_distance = distances.get('earliestDistanceToMP', 'N/A')     # Earliest arrival to MP distance
                        latest_distance = distances.get('latestDistanceToMP', 'N/A')         # Latest arrival to MP distance
                        custom_route_remaining = distances.get('customRouteRemaining', None) # Custom route remaining distance
                        
                        # 🔧 Enhanced turning point extraction using new function
                        cut_out_point, cut_out_distance, custom_route_info = extract_turning_point_info(aircraft)
                        
                        # 🆕 Special indicator for CSN202 auto command status
                        auto_cmd_indicator = ""
                        if callsign == 'CSN202':
                            if csn202_auto_commands_sent:
                                auto_cmd_indicator = " 🤖已发送自动指令"
                            elif earliest_distance != 'N/A':
                                try:
                                    ed_float = float(earliest_distance)
                                    if ed_float <= csn202_target_distance + 10:  # Show warning when approaching
                                        auto_cmd_indicator = f" 🎯即将触发自动指令(目标:{csn202_target_distance}nm)"
                                except:
                                    pass
                        
                        # Handle special statuses
                        if isinstance(flexibility, dict):
                            status = flexibility.get('status', 'ON_ROUTE')
                            arc_type = flexibility.get('arcType', 'N/A')
                            
                            if status == 'OFF_ROUTE':
                                # Off route (heading/direct mode)
                                flexible_str = f" 🎯MP[直飞:{current_direct}nm ⚠️脱离航路({arc_type}模式)]{auto_cmd_indicator}"
                            elif status in ['COMMITTED_TO_MP', 'DIRECT_TO_MP']:
                                # 🔧 Enhanced custom route/direct to MP display
                                
                                # 🔧 Additional debug for CSN202
                                if callsign == 'CSN202':
                                    print(f"🔍 {callsign} Enhanced处理:")
                                    print(f"   status: {status}")
                                    print(f"   cut_out_point: {cut_out_point}")
                                    print(f"   cut_out_distance: {cut_out_distance}")
                                    print(f"   custom_route_remaining: {custom_route_remaining}")
                                    if custom_route_info:
                                        print(f"   custom_route_info: {custom_route_info}")
                                
                                if status == 'DIRECT_TO_MP':
                                    # Direct to MP mode
                                    flexible_str = f" 🎯MP[直飞:{current_direct}nm 📋直接到MP]{auto_cmd_indicator}"
                                elif custom_route_remaining and custom_route_remaining != 'N/A':
                                    # Custom route mode with detailed info
                                    if cut_out_point and cut_out_point != 'N/A':
                                        if cut_out_distance and cut_out_distance != 'N/A':
                                            # Full info: path + turning point with distance + direct
                                            if custom_route_info:
                                                progress_info = custom_route_info.get('progress', 'N/A')
                                                flexible_str = f" 🎯MP[路径:{custom_route_remaining}nm 转弯点({cut_out_point}):{cut_out_distance}nm 直飞:{current_direct}nm 📋已接受custom route {progress_info}]{auto_cmd_indicator}"
                                            else:
                                                flexible_str = f" 🎯MP[路径:{custom_route_remaining}nm 转弯点({cut_out_point}):{cut_out_distance}nm 直飞:{current_direct}nm 📋已接受custom route]{auto_cmd_indicator}"
                                        else:
                                            # Turning point name but no distance
                                            flexible_str = f" 🎯MP[路径:{custom_route_remaining}nm 转弯点({cut_out_point}):N/A 直飞:{current_direct}nm 📋已接受custom route]{auto_cmd_indicator}"
                                    else:
                                        # No turning point info
                                        flexible_str = f" 🎯MP[路径:{custom_route_remaining}nm 直飞:{current_direct}nm 📋已接受custom route]{auto_cmd_indicator}"
                                else:
                                    # No custom route remaining distance, might be direct to MP
                                    if cut_out_point and cut_out_point != 'N/A':
                                        flexible_str = f" 🎯MP[转弯点({cut_out_point}) 直飞:{current_direct}nm 📋已接受custom route]{auto_cmd_indicator}"
                                    else:
                                        flexible_str = f" 🎯MP[直飞:{current_direct}nm 📋已接受custom route]{auto_cmd_indicator}"
                            elif status == 'PAST_ARC':
                                # Past arc area
                                flexible_str = f" 🎯MP[直飞:{current_direct}nm ⚠️已过弧线区域]{auto_cmd_indicator}"
                            elif status == 'PAST_EARLIEST':
                                # Past earliest cut-out point
                                latest_cut = flexibility.get('latestCutOut', 'N/A')
                                flexible_str = f" 🎯MP[直飞:{current_direct}nm ⚠️已过最早切出点 最晚:{latest_distance}nm({latest_cut})]{auto_cmd_indicator}"
                            else:
                                # Normal status
                                display_range = flexibility.get('displayRange', f"{flexibility.get('earliestCutOut', 'N/A')}→{flexibility.get('latestCutOut', 'N/A')}")
                                
                                if earliest_distance != 'N/A' and latest_distance != 'N/A':
                                    flexible_str = f" 🎯MP[直飞:{current_direct}nm 最早:{earliest_distance}nm 最晚:{latest_distance}nm {arc_type}({display_range})]{auto_cmd_indicator}"
                                else:
                                    flexible_str = f" 🎯MP[直飞:{current_direct}nm {arc_type}({display_range})]{auto_cmd_indicator}"
                        else:
                            flexible_str = f" 🎯MP[直飞:{current_direct}nm]{auto_cmd_indicator}"
                
                # Build display string
                type_info = f"{aircraft_type}/{flight_type}"
                vs_display = f" VS:{vertical_speed}" if vertical_speed != 0 else ""
                next_wp_display = f" →{next_waypoint}" if next_waypoint != 'N/A' and next_waypoint != 'END' else ""
                climb_display = f" {climb_phase.upper()}" if climb_phase else ""
                
                # Show route info for arrival flights
                route_display = f" ({planned_route})" if planned_route != 'N/A' and 'Arrival' in planned_route else ""
                
                print(f"  ✈️  {callsign}({type_info}){route_display}: ALT={altitude} {speed_str} {direction_str}{vs_display} [{mode}]{next_wp_display}{climb_display}{flexible_str}")
                
                # 🆕 Extra debug for custom route flights
                if callsign == 'CSN202' and nav_data.get('customRoute'):
                    custom_route_nav = nav_data.get('customRoute')
                    print(f"🛣️ {callsign} Custom Route详情:")
                    print(f"   完整路径: {' → '.join(custom_route_nav.get('waypoints', []))}")
                    print(f"   当前进度: {custom_route_nav.get('progress', 'N/A')}")
                    print(f"   转弯点: {custom_route_nav.get('turningPoint', 'N/A')}")
                    print(f"   剩余距离: {custom_route_nav.get('remainingDistance', 'N/A')}nm")
                
            except Exception as e:
                print(f"  ❌ Error processing aircraft data: {e}")
                print(f"      Aircraft data: {aircraft}")
    
    print("-" * 100)  # Separator line

@socketio.on('test_message')
def handle_test_message(data):
    print(f"📨 Received frontend test message: {data}")
    emit('test_response', {'message': 'Enhanced backend received test message'})

def send_csn202_heading_120():
    """Send CSN202 heading 120 command"""
    if is_connected:
        command = {
            'callsign': 'CSN202',
            'instructions': {
                'heading': 120
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Command sent: CSN202 -> Heading 120°")
        return True
    else:
        print("❌ Frontend not connected, cannot send command")
        return False

def send_test_altitude():
    """Send CSN202 altitude command"""
    if is_connected:
        command = {
            'callsign': 'CSN202',
            'instructions': {
                'altitude': 'FL300'
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Altitude command sent: CSN202 -> FL300")
        return True
    else:
        print("❌ Frontend not connected")
        return False

def send_test_speed():
    """Send CSN202 speed command"""
    if is_connected:
        command = {
            'callsign': 'CSN202',
            'instructions': {
                'speed': 280
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Speed command sent: CSN202 -> 280 knots")
        return True
    else:
        print("❌ Frontend not connected")
        return False

def send_combo_command():
    """Send CSN202 combined command"""
    if is_connected:
        command = {
            'callsign': 'CSN202',
            'instructions': {
                'altitude': 'FL350',
                'speed': 300,
                'heading': 180
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Combined command sent: CSN202 -> Altitude FL350, Speed 300, Heading 180°")
        return True
    else:
        print("❌ Frontend not connected")
        return False

def send_direct_to_mp(callsign):
    """Send direct to MP command"""
    if is_connected:
        command = {
            'callsign': callsign,
            'instructions': {
                'directToMP': True
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Direct to MP command sent: {callsign}")
        return True
    else:
        print("❌ Frontend not connected")
        return False

# 🆕 New command: Send custom route to aircraft
def send_custom_route(callsign, waypoints):
    """Send custom route command"""
    if is_connected:
        command = {
            'callsign': callsign,
            'instructions': {
                'customRoute': waypoints
            }
        }
        
        socketio.emit('atc_commands', [command])
        print(f"🎯 Custom route command sent: {callsign} -> {' → '.join(waypoints)}")
        return True
    else:
        print("❌ Frontend not connected")
        return False

# 🆕 Reset auto command flag (for testing)
def reset_csn202_auto_commands():
    """Reset CSN202 auto command flag"""
    global csn202_auto_commands_sent
    csn202_auto_commands_sent = False
    print("🔄 CSN202 auto command flag reset")
    return True

# Manual control routes
@app.route('/send_heading', methods=['GET', 'POST'])
def manual_send_heading():
    success = send_csn202_heading_120()
    return {'success': success, 'message': 'CSN202 heading 120 command sent'}

@app.route('/send_altitude', methods=['GET', 'POST'])
def manual_send_altitude():
    success = send_test_altitude()
    return {'success': success, 'message': 'CSN202 altitude FL300 command sent'}

@app.route('/send_speed', methods=['GET', 'POST'])
def manual_send_speed():
    success = send_test_speed()
    return {'success': success, 'message': 'CSN202 speed 280 command sent'}

@app.route('/send_combo', methods=['GET', 'POST'])
def manual_send_combo():
    success = send_combo_command()
    return {'success': success, 'message': 'CSN202 combined command sent'}

@app.route('/send_direct_mp/<callsign>', methods=['GET', 'POST'])
def manual_send_direct_mp(callsign):
    success = send_direct_to_mp(callsign)
    return {'success': success, 'message': f'{callsign} direct to MP command sent'}

# 🆕 New route for custom route command
@app.route('/send_custom_route/<callsign>/<path:waypoints>', methods=['GET', 'POST'])
def manual_send_custom_route(callsign, waypoints):
    waypoint_list = waypoints.split(',')
    success = send_custom_route(callsign, waypoint_list)
    return {'success': success, 'message': f'{callsign} custom route {" → ".join(waypoint_list)} command sent'}

# 🆕 Reset auto command flag route
@app.route('/reset_auto_commands', methods=['GET', 'POST'])
def manual_reset_auto_commands():
    success = reset_csn202_auto_commands()
    return {'success': success, 'message': 'CSN202 auto command flag reset'}

# 🆕 Manual trigger auto commands (for testing)
@app.route('/trigger_auto_commands', methods=['GET', 'POST'])
def manual_trigger_auto_commands():
    # Find CSN202 in current aircraft data and trigger auto commands
    for aircraft in aircraft_data:
        if aircraft and isinstance(aircraft, dict) and aircraft.get('callsign') == 'CSN202':
            speed_data = aircraft.get('speed', {})
            ground_speed = 300  # Default
            
            if isinstance(speed_data, dict):
                gs = speed_data.get('groundSpeed', None)
                if gs and gs != 'N/A':
                    try:
                        ground_speed = float(gs)
                    except:
                        ground_speed = 300
            
            success = send_csn202_auto_descent_and_route(ground_speed)
            return {'success': success, 'message': f'CSN202 auto commands triggered manually (GS: {ground_speed})'}
    
    return {'success': False, 'message': 'CSN202 not found in current aircraft data'}

@app.route('/aircraft_status', methods=['GET'])
def get_aircraft_status():
    if aircraft_data and isinstance(aircraft_data, list):
        status = []
        for aircraft in aircraft_data:
            if aircraft and isinstance(aircraft, dict):
                try:
                    # Extract basic data
                    pos = aircraft.get('position', {})
                    speed_data = aircraft.get('speed', {})
                    direction_data = aircraft.get('direction', {})
                    vertical_data = aircraft.get('vertical', {})
                    nav_data = aircraft.get('navigation', {})
                    flexible_data = aircraft.get('flexibleApproach', {})
                    
                    aircraft_info = {
                        'callsign': aircraft.get('callsign', 'N/A'),
                        'aircraftType': aircraft.get('aircraftType', 'N/A'),
                        'flightType': aircraft.get('type', 'N/A'),
                        'altitude': pos.get('altitude', 'N/A') if isinstance(pos, dict) else 'N/A',
                        'speed': {
                            'ias': speed_data.get('ias', 'N/A') if isinstance(speed_data, dict) else 'N/A',
                            'tas': speed_data.get('tas', 'N/A') if isinstance(speed_data, dict) else 'N/A',
                            'groundSpeed': speed_data.get('groundSpeed', 'N/A') if isinstance(speed_data, dict) else 'N/A'
                        },
                        'direction': {
                            'heading': direction_data.get('heading', 'N/A') if isinstance(direction_data, dict) else 'N/A',
                            'track': direction_data.get('track', 'N/A') if isinstance(direction_data, dict) else 'N/A'
                        },
                        'vertical': {
                            'verticalSpeed': vertical_data.get('verticalSpeed', 'N/A') if isinstance(vertical_data, dict) else 'N/A',
                            'targetAltitude': vertical_data.get('targetAltitude', 'N/A') if isinstance(vertical_data, dict) else 'N/A'
                        },
                        'navigation': {
                            'mode': nav_data.get('mode', 'N/A') if isinstance(nav_data, dict) else 'N/A',
                            'plannedRoute': nav_data.get('plannedRoute', 'N/A') if isinstance(nav_data, dict) else 'N/A',
                            'currentWaypoint': nav_data.get('currentWaypoint', 'N/A') if isinstance(nav_data, dict) else 'N/A',
                            'nextWaypoint': nav_data.get('nextWaypoint', 'N/A') if isinstance(nav_data, dict) else 'N/A',
                            'climbPhase': nav_data.get('climbPhase', None) if isinstance(nav_data, dict) else None,
                            # 🆕 Include custom route data in API response
                            'customRoute': nav_data.get('customRoute', None) if isinstance(nav_data, dict) else None
                        },
                        # Add flexible approach data (3 key distances)
                        'flexibleApproach': flexible_data if isinstance(flexible_data, dict) else None
                    }
                    status.append(aircraft_info)
                except Exception as e:
                    print(f"Error processing aircraft for status: {e}")
        
        # Include simulation info and auto command status
        response = {
            'aircraft_count': len(status), 
            'aircraft': status,
            'simulation_time': full_data.get('simulationTimeFormatted', 'N/A'),
            'simulation_running': full_data.get('isRunning', False),
            'simulation_speed': full_data.get('simulationSpeed', 1),
            'mpPoint': full_data.get('mpPoint', {}),
            'routeFlexibility': full_data.get('routeFlexibility', {}),
            # 🆕 Auto command status
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }
        return response
    else:
        return {
            'aircraft_count': 0, 
            'aircraft': [], 
            'message': 'No aircraft data available',
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }

@app.route('/simulation_status', methods=['GET'])
def get_simulation_status():
    if isinstance(full_data, dict):
        return {
            'simulation_time': full_data.get('simulationTime', 0),
            'simulation_time_formatted': full_data.get('simulationTimeFormatted', '00:00:00'),
            'is_running': full_data.get('isRunning', False),
            'simulation_speed': full_data.get('simulationSpeed', 1),
            'aircraft_count': full_data.get('aircraftCount', 0),
            'last_update': full_data.get('timestamp', 'N/A'),
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }
    else:
        return {
            'simulation_time': 0,
            'simulation_time_formatted': '00:00:00',
            'is_running': False,
            'simulation_speed': 1,
            'aircraft_count': 0,
            'last_update': 'N/A',
            'message': 'No simulation data available',
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }

# Get only flexible approach summary
@app.route('/mp_distances', methods=['GET'])
def get_mp_distances():
    """Get simplified MP distance data for all arrival flights"""
    if aircraft_data and isinstance(aircraft_data, list):
        mp_data = []
        for aircraft in aircraft_data:
            if aircraft and isinstance(aircraft, dict):
                flexible_data = aircraft.get('flexibleApproach', None)
                if flexible_data and isinstance(flexible_data, dict):
                    distances = flexible_data.get('distances', {})
                    flexibility = flexible_data.get('flexibility', {})
                    
                    # 🆕 Include custom route info in MP distances
                    nav_data = aircraft.get('navigation', {})
                    custom_route_data = nav_data.get('customRoute', None) if isinstance(nav_data, dict) else None
                    
                    mp_info = {
                        'callsign': aircraft.get('callsign', 'N/A'),
                        'route': nav_data.get('plannedRoute', 'N/A') if isinstance(nav_data, dict) else 'N/A',
                        'currentDirectToMP': distances.get('currentDirectToMP', 'N/A'),    # Current position direct to MP
                        'earliestDistanceToMP': distances.get('earliestDistanceToMP', 'N/A'), # Earliest arrival distance
                        'latestDistanceToMP': distances.get('latestDistanceToMP', 'N/A'),     # Latest arrival distance
                        'arcType': flexibility.get('arcType', 'N/A'),
                        'earliestCutOut': flexibility.get('earliestCutOut', 'N/A'),
                        'latestCutOut': flexibility.get('latestCutOut', 'N/A'),
                        'status': flexibility.get('status', 'N/A'),
                        # 🆕 Add custom route summary
                        'customRoute': {
                            'waypoints': custom_route_data.get('waypoints', []) if custom_route_data else [],
                            'turningPoint': custom_route_data.get('turningPoint', None) if custom_route_data else None,
                            'remainingDistance': custom_route_data.get('remainingDistance', None) if custom_route_data else None,
                            'progress': custom_route_data.get('progress', None) if custom_route_data else None
                        } if custom_route_data else None
                    }
                    mp_data.append(mp_info)
        
        return {
            'arrival_flights_count': len(mp_data),
            'mp_distances': mp_data,
            'mpPoint': full_data.get('mpPoint', {}),
            'timestamp': full_data.get('timestamp', 'N/A'),
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }
    else:
        return {
            'arrival_flights_count': 0,
            'mp_distances': [],
            'message': 'No MP distance data available',
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }

# 🆕 New endpoint: Get custom route details for all aircraft
@app.route('/custom_routes', methods=['GET'])
def get_custom_routes():
    """Get detailed custom route information for all aircraft"""
    if aircraft_data and isinstance(aircraft_data, list):
        custom_routes = []
        for aircraft in aircraft_data:
            if aircraft and isinstance(aircraft, dict):
                nav_data = aircraft.get('navigation', {})
                if isinstance(nav_data, dict) and nav_data.get('customRoute'):
                    custom_route_data = nav_data.get('customRoute')
                    custom_routes.append({
                        'callsign': aircraft.get('callsign', 'N/A'),
                        'aircraftType': aircraft.get('aircraftType', 'N/A'),
                        'mode': nav_data.get('mode', 'N/A'),
                        'customRoute': custom_route_data
                    })
        
        return {
            'custom_route_count': len(custom_routes),
            'custom_routes': custom_routes,
            'timestamp': full_data.get('timestamp', 'N/A'),
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }
    else:
        return {
            'custom_route_count': 0,
            'custom_routes': [],
            'message': 'No custom route data available',
            'csn202_auto_commands_sent': csn202_auto_commands_sent,
            'csn202_target_distance': csn202_target_distance
        }

if __name__ == '__main__':
    print("🚀 Enhanced Aircraft Data Monitoring Backend Starting...")
    print("📡 Waiting for frontend connection...")
    print("📊 Enhanced: Now supports navigation.customRoute data")
    print("🤖 NEW: Auto commands for CSN202 at 70nm from MP")
    print("\n💡 Manual test URLs:")
    print("   http://localhost:5000/send_heading               - Send heading command")
    print("   http://localhost:5000/send_altitude              - Send altitude command")
    print("   http://localhost:5000/send_speed                 - Send speed command")
    print("   http://localhost:5000/send_combo                 - Send combined command")
    print("   http://localhost:5000/send_direct_mp/CCA101      - Send direct to MP command")
    print("   http://localhost:5000/send_custom_route/CSN202/R21,MP - Send custom route command")
    print("   http://localhost:5000/reset_auto_commands        - Reset CSN202 auto command flag")
    print("   http://localhost:5000/trigger_auto_commands      - Manually trigger CSN202 auto commands")
    print("   http://localhost:5000/aircraft_status            - View all aircraft status")
    print("   http://localhost:5000/simulation_status          - View simulation status")
    print("   http://localhost:5000/mp_distances               - View MP distances only")
    print("   http://localhost:5000/custom_routes              - View custom routes only")
    print("\n🤖 Auto Command Logic:")
    print("   🎯 Target: CSN202 at 70nm earliest distance to MP")
    print("   ⬇️  Action 1: Descent to FL020 (descent rate = ground speed × 5)")
    print("   🛣️  Action 2: Custom route IR15 → IR5 → MP")
    print("   🔒 One-time trigger (reset with /reset_auto_commands)")
    print("\n📋 Enhanced Display format:")
    print("   🎯MP[直飞:15.2nm 最早:25.3nm 最晚:38.7nm inner(IR24→IL17)]")
    print("   🎯MP[路径:79.8nm 转弯点(R21):22.0nm 直飞:35.2nm 📋已接受custom route 1/2] 🤖已发送自动指令")
    print("="*80)
    
    # Start Flask server
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
