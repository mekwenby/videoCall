import random
import string
import asyncio
import json
import logging
from flask import Flask, render_template
from flask_socketio import SocketIO, emit, join_room, leave_room
import socketio as sio
from media_relay import relay_server

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'GKJSXGF$^LKSHDKLJSHF2026'

# 使用 eventlet 作为异步模式
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 存储在线用户 {sid: {'username': str, 'room': str}}
online_users = {}

# 存储房间信息 {room_id: {'users': [sid1, sid2], 'streams': {sid: stream_info}}}
rooms = {}

# 存储用户的媒体流信息 {sid: {'video': bool, 'audio': bool}}
user_streams = {}


def generate_username():
    """生成随机用户名 User_xxxxx"""
    while True:
        random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        username = f"Call_{random_str}"
        if username not in [u.get('username') for u in online_users.values()]:
            return username


@app.route('/')
def index():
    """首页"""
    return render_template('index_relay.html')


@socketio.on('connect')
def handle_connect():
    """用户连接时自动生成用户名并加入在线列表"""
    from flask import request
    username = generate_username()
    online_users[request.sid] = {
        'username': username,
        'room': None
    }
    join_room(request.sid)
    
    # 通知当前用户其用户名
    emit('user_assigned', {'username': username})
    
    # 广播在线用户列表给所有用户
    broadcast_user_list()
    logger.info(f"用户连接: {username} ({request.sid})")


@socketio.on('disconnect')
def handle_disconnect():
    """用户断开连接时清理"""
    from flask import request
    sid = request.sid
    if sid in online_users:
        user_info = online_users[sid]
        username = user_info['username']
        room_id = user_info.get('room')
        
        # 如果用户在房间中，离开房间
        if room_id and room_id in rooms:
            leave_room_internal(sid, room_id)
        
        del online_users[sid]
        if sid in user_streams:
            del user_streams[sid]
        
        leave_room(sid)
        broadcast_user_list()
        logger.info(f"用户断开: {username} ({sid})")


def broadcast_user_list():
    """广播在线用户列表给所有连接的用户"""
    user_list = []
    for sid, info in online_users.items():
        user_list.append({
            'username': info['username'],
            'sid': sid,
            'in_call': info.get('room') is not None
        })
    socketio.emit('user_list', {'users': user_list})


@socketio.on('call_request')
def handle_call_request(data):
    """处理通话请求"""
    from flask import request
    target_sid = data.get('target_sid')
    caller_sid = request.sid
    
    if target_sid not in online_users:
        emit('call_error', {'message': '用户已离线'})
        return
    
    if target_sid == caller_sid:
        emit('call_error', {'message': '不能呼叫自己'})
        return
    
    # 检查对方是否已在通话中
    if online_users[target_sid].get('room'):
        emit('call_error', {'message': '对方正在通话中'})
        return
    
    caller_name = online_users[caller_sid]['username']
    target_name = online_users[target_sid]['username']
    
    # 创建通话房间
    room_id = f"room_{caller_sid}_{target_sid}"
    rooms[room_id] = {
        'users': [caller_sid, target_sid],
        'streams': {}
    }
    
    # 将双方加入房间
    online_users[caller_sid]['room'] = room_id
    online_users[target_sid]['room'] = room_id
    join_room(room_id, sid=caller_sid)
    join_room(room_id, sid=target_sid)
    
    # 通知被呼叫方
    emit('incoming_call', {
        'caller_sid': caller_sid,
        'caller_name': caller_name,
        'room_id': room_id
    }, room=target_sid)
    
    # 通知呼叫方等待中
    emit('call_ringing', {
        'target_sid': target_sid,
        'target_name': target_name,
        'room_id': room_id
    })
    
    broadcast_user_list()
    logger.info(f"通话请求: {caller_name} -> {target_name}, 房间: {room_id}")


@socketio.on('call_response')
def handle_call_response(data):
    """处理通话响应（接听/拒绝）"""
    from flask import request
    room_id = data.get('room_id')
    accepted = data.get('accepted', False)
    caller_sid = data.get('caller_sid')
    callee_sid = request.sid
    
    if room_id not in rooms:
        # 房间不存在，通知双方关闭弹窗
        emit('call_rejected', {'reason': '通话已取消'})
        if caller_sid and caller_sid in online_users:
            emit('call_rejected', {'reason': '通话已取消'}, room=caller_sid)
        return
    
    if accepted:
        # 接听，通知双方开始建立连接
        room = rooms[room_id]
        
        # 通知双方通话已建立，准备交换媒体
        emit('call_accepted', {
            'room_id': room_id,
            'peer_sid': callee_sid,
            'is_caller': True
        }, room=caller_sid)
        
        emit('call_accepted', {
            'room_id': room_id,
            'peer_sid': caller_sid,
            'is_caller': False
        }, room=callee_sid)
        
        logger.info(f"通话已接听: {room_id}")
    else:
        # 拒绝，清理房间
        leave_room_internal(caller_sid, room_id)
        leave_room_internal(callee_sid, room_id)
        if room_id in rooms:
            del rooms[room_id]
        
        # 通知双方通话被拒绝/取消
        emit('call_rejected', {'reason': '对方拒绝了通话'}, room=caller_sid)
        broadcast_user_list()
        logger.info(f"通话被拒绝: {room_id}")


@socketio.on('join_room')
def handle_join_room(data):
    """用户加入房间准备媒体交换"""
    from flask import request
    room_id = data.get('room_id')
    sid = request.sid
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # 通知房间内其他用户有新用户加入
    for other_sid in room['users']:
        if other_sid != sid and other_sid in online_users:
            emit('peer_joined', {
                'peer_sid': sid,
                'username': online_users[sid]['username']
            }, room=other_sid)
    
    logger.info(f"用户 {online_users[sid]['username']} 加入房间 {room_id}")


@socketio.on('offer')
def handle_offer(data):
    """转发 WebRTC Offer (服务器中转模式)"""
    from flask import request
    room_id = data.get('room_id')
    offer = data.get('offer')
    sid = request.sid
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # 转发给房间内其他用户
    for other_sid in room['users']:
        if other_sid != sid:
            emit('offer', {
                'offer': offer,
                'from_sid': sid,
                'from_name': online_users[sid]['username']
            }, room=other_sid)
            logger.info(f"转发 Offer: {sid} -> {other_sid}")


@socketio.on('answer')
def handle_answer(data):
    """转发 WebRTC Answer"""
    from flask import request
    room_id = data.get('room_id')
    answer = data.get('answer')
    sid = request.sid
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # 转发给房间内其他用户
    for other_sid in room['users']:
        if other_sid != sid:
            emit('answer', {
                'answer': answer,
                'from_sid': sid
            }, room=other_sid)
            logger.info(f"转发 Answer: {sid} -> {other_sid}")


@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    """转发 ICE Candidate"""
    from flask import request
    room_id = data.get('room_id')
    candidate = data.get('candidate')
    sid = request.sid
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # 转发给房间内其他用户
    for other_sid in room['users']:
        if other_sid != sid:
            emit('ice_candidate', {
                'candidate': candidate,
                'from_sid': sid
            }, room=other_sid)


@socketio.on('stream_ready')
def handle_stream_ready(data):
    """通知其他用户流已准备好"""
    from flask import request
    room_id = data.get('room_id')
    stream_type = data.get('type')  # 'video' or 'audio' or 'both'
    sid = request.sid
    
    if room_id not in rooms:
        return
    
    user_streams[sid] = {
        'video': stream_type in ['video', 'both'],
        'audio': stream_type in ['audio', 'both']
    }
    
    room = rooms[room_id]
    for other_sid in room['users']:
        if other_sid != sid:
            emit('peer_stream_ready', {
                'peer_sid': sid,
                'type': stream_type
            }, room=other_sid)


@socketio.on('end_call')
def handle_end_call(data):
    """处理挂断"""
    from flask import request
    room_id = data.get('room_id')
    sid = request.sid
    
    if room_id and room_id in rooms:
        room = rooms[room_id]
        
        # 通知房间内其他用户
        for other_sid in room['users']:
            if other_sid != sid:
                emit('call_ended', {'reason': '对方已挂断'}, room=other_sid)
        
        # 清理房间
        for user_sid in room['users']:
            try:
                leave_room_internal(user_sid, room_id)
            except Exception as e:
                logger.warning(f"离开房间失败 {user_sid}: {e}")

        # 确保房间始终被删除
        if room_id in rooms:
            del rooms[room_id]
        broadcast_user_list()
        logger.info(f"通话结束: {room_id}")


def leave_room_internal(sid, room_id):
    """内部方法：用户离开房间"""
    if sid in online_users:
        online_users[sid]['room'] = None
    leave_room(room_id, sid=sid)


# ============ 中继模式信令处理 ============

@socketio.on('request_relay')
def handle_request_relay(data):
    """客户端请求切换到中继模式 (P2P 连接失败)"""
    from flask import request
    room_id = data.get('room_id')
    sid = request.sid

    if room_id not in rooms:
        emit('relay_error', {'message': '房间不存在'})
        return

    room = rooms[room_id]
    if sid not in room['users']:
        emit('relay_error', {'message': '你不是该房间的成员'})
        return

    # 启用房间的中继模式
    room['relay_enabled'] = True

    # 获取该用户在房间中的角色 (呼叫方还是被呼叫方)
    caller_sid = room['users'][0]
    is_caller = (sid == caller_sid)

    # 通知房间内所有用户切换到中继模式
    for user_sid in room['users']:
        emit('relay_enabled', {
            'room_id': room_id,
            'peer_sid': room['users'][1] if user_sid == room['users'][0] else room['users'][0],
            'is_relay_caller': is_caller
        }, room=user_sid)

    logger.info(f"房间 {room_id} 启用中继模式")


@socketio.on('relay_offer')
def handle_relay_offer(data):
    """转发中继模式的 WebRTC Offer"""
    from flask import request
    room_id = data.get('room_id')
    offer = data.get('offer')
    sid = request.sid

    if room_id not in rooms:
        return

    room = rooms[room_id]
    if not room.get('relay_enabled'):
        return

    # 确定是呼叫方还是被呼叫方
    caller_sid = room['users'][0]
    is_caller_offer = (sid == caller_sid)

    try:
        if is_caller_offer:
            # 呼叫方发起 offer，服务器创建 answer
            answer = relay_server.relay_offer_from_caller(room_id, offer['sdp'], offer.get('type', 'offer'))
            # 转发 answer 给呼叫方
            emit('relay_answer', {
                'answer': answer,
                'from_sid': 'relay_server'
            }, room=sid)
            # 通知被呼叫方有 offer
            callee_sid = room['users'][1]
            emit('relay_offer', {
                'offer': offer,
                'from_sid': 'relay_server'
            }, room=callee_sid)
        else:
            # 被呼叫方发起 offer
            answer = relay_server.relay_offer_from_callee(room_id, offer['sdp'], offer.get('type', 'offer'))
            emit('relay_answer', {
                'answer': answer,
                'from_sid': 'relay_server'
            }, room=sid)
            caller_sid = room['users'][0]
            emit('relay_offer', {
                'offer': offer,
                'from_sid': 'relay_server'
            }, room=caller_sid)
    except Exception as e:
        logger.error(f"中继 offer 处理失败: {e}")
        emit('relay_error', {'message': str(e)}, room=sid)


@socketio.on('relay_answer')
def handle_relay_answer(data):
    """转发中继模式的 WebRTC Answer"""
    from flask import request
    room_id = data.get('room_id')
    answer = data.get('answer')
    sid = request.sid

    if room_id not in rooms:
        return

    room = rooms[room_id]
    if not room.get('relay_enabled'):
        return

    # 确定是呼叫方还是被呼叫方
    caller_sid = room['users'][0]
    is_caller = (sid == caller_sid)

    # 正确处理 SDP answer（不是 ICE candidate）
    relay_server.handle_relay_answer(room_id, answer.get('sdp', ''), answer.get('type', 'answer'), is_caller)


@socketio.on('relay_ice_candidate')
def handle_relay_ice_candidate(data):
    """转发中继模式的 ICE Candidate"""
    from flask import request
    room_id = data.get('room_id')
    candidate = data.get('candidate')
    sid = request.sid

    if room_id not in rooms:
        return

    room = rooms[room_id]
    if not room.get('relay_enabled'):
        return

    # 确定发送方是呼叫方还是被呼叫方
    caller_sid = room['users'][0]
    is_caller = (sid == caller_sid)

    # 转发 ICE candidate 到中继服务器（relay 模式下不转发给 peer）
    relay_server.add_relay_candidate(room_id, candidate, is_caller)


@socketio.on('relay_end')
def handle_relay_end(data):
    """中继模式下结束通话"""
    from flask import request
    room_id = data.get('room_id')
    sid = request.sid

    if room_id in rooms:
        room = rooms[room_id]
        # 关闭中继连接
        relay_server.close_relay_room(room_id)

        # 通知房间内其他用户
        for other_sid in room['users']:
            if other_sid != sid:
                emit('call_ended', {'reason': '对方已挂断'}, room=other_sid)

        # 清理房间
        for user_sid in room['users']:
            try:
                leave_room_internal(user_sid, room_id)
            except Exception as e:
                logger.warning(f"离开房间失败 {user_sid}: {e}")

        # 确保房间始终被删除
        if room_id in rooms:
            del rooms[room_id]
        broadcast_user_list()
        logger.info(f"中继通话结束: {room_id}")


if __name__ == '__main__':
    # 启动媒体中继服务
    relay_server.start()
    print("媒体中继服务已启动 (P2P 失败时的兜底方案)")

    print("启动视频通话服务器 (服务器中转模式)...")
    print("访问地址: http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
