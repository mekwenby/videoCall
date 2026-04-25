"""
aiortc 媒体桥接服务 - P2P 失败时的中继方案
在独立线程中运行 asyncio 事件循环处理 WebRTC 中继连接
"""

import asyncio
import logging
import threading
from typing import Dict, Optional

from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

logger = logging.getLogger(__name__)


class MediaRelayServer:
    """媒体中继服务器 - 使用 aiortc 在 P2P 失败时桥接媒体流"""

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        # room_id -> {pc_caller: RTCPeerConnection, pc_callee: RTCPeerConnection, relay: MediaRelay}
        self.rooms: Dict[str, dict] = {}
        self._started = False

    def start(self):
        """启动异步事件循环线程"""
        if self._started:
            return
        self._started = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("媒体中继服务已启动")

    def _run_loop(self):
        """在独立线程中运行事件循环"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _run_async(self, coro):
        """在线程安全的方式下运行异步协程"""
        if self.loop is None:
            raise RuntimeError("中继服务未启动")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=30)

    async def _create_room(self, room_id: str) -> dict:
        """为指定房间创建中继连接"""
        relay = MediaRelay()
        pc_caller = RTCPeerConnection()
        pc_callee = RTCPeerConnection()

        room_data = {
            'relay': relay,
            'pc_caller': pc_caller,
            'pc_callee': pc_callee,
            'caller_track': None,
            'callee_track': None
        }
        self.rooms[room_id] = room_data

        # pc_caller 处理来自呼叫方的连接
        @pc_caller.on("track")
        def on_caller_track(track):
            logger.info(f"中继收到呼叫方媒体轨道: {track.kind}")
            room_data['caller_track'] = track
            # 将呼叫方的轨道转发给被呼叫方
            new_track = relay.subscribe(track)
            pc_callee.addTrack(new_track)

        # pc_callee 处理来自被呼叫方的连接
        @pc_callee.on("track")
        def on_callee_track(track):
            logger.info(f"中继收到被呼叫方媒体轨道: {track.kind}")
            room_data['callee_track'] = track
            # 将被呼叫方的轨道转发给呼叫方
            new_track = relay.subscribe(track)
            pc_caller.addTrack(new_track)

        return room_data

    async def _handle_offer(self, room_id: str, offer_sdp: str, offer_type: str, is_caller_offer: bool):
        """
        处理中继模式的 WebRTC offer
        当 is_caller_offer=True 时，offer 来自呼叫方，pc_caller 处理
        当 is_caller_offer=False 时，offer 来自被呼叫方，pc_callee 处理
        """
        if room_id not in self.rooms:
            await self._create_room(room_id)

        room = self.rooms[room_id]

        if is_caller_offer:
            pc_offer = room['pc_caller']
            pc_answer = room['pc_callee']
        else:
            pc_offer = room['pc_callee']
            pc_answer = room['pc_caller']

        # 设置 offer 方的远端描述
        await pc_offer.setRemoteDescription(RTCSessionDescription(
            sdp=offer_sdp,
            type=offer_type
        ))

        # 创建 answer
        answer = await pc_offer.createAnswer()
        await pc_offer.setLocalDescription(answer)

        return {
            'sdp': answer.sdp,
            'type': answer.type
        }

    async def _handle_relay_answer(self, room_id: str, answer_sdp: str, answer_type: str, is_caller: bool):
        """处理中继模式的 answer，设置远端描述"""
        if room_id not in self.rooms:
            return

        room = self.rooms[room_id]
        pc = room['pc_caller'] if is_caller else room['pc_callee']

        try:
            await pc.setRemoteDescription(RTCSessionDescription(
                sdp=answer_sdp,
                type=answer_type
            ))
        except Exception as e:
            logger.warning(f"设置 Answer 失败: {e}")

    async def _handle_ice_candidate(self, room_id: str, candidate: dict, is_caller: bool):
        """处理 ICE candidate 中继"""
        if room_id not in self.rooms:
            return

        room = self.rooms[room_id]
        pc = room['pc_caller'] if is_caller else room['pc_callee']

        try:
            await pc.addIceCandidate(RTCIceCandidate(candidate))
        except Exception as e:
            logger.warning(f"添加 ICE candidate 失败: {e}")

    async def _close_room(self, room_id: str):
        """关闭房间的中继连接"""
        if room_id not in self.rooms:
            return

        room = self.rooms[room_id]
        try:
            await room['pc_caller'].close()
        except Exception:
            pass
        try:
            await room['pc_callee'].close()
        except Exception:
            pass
        del self.rooms[room_id]
        logger.info(f"中继房间已关闭: {room_id}")

    def create_relay_offer(self, room_id: str) -> dict:
        """
        创建中继 offer - 由服务器主动创建，发送给客户端
        这是让客户端连接中继服务器的方式
        """
        return self._run_async(self._create_relay_offer_async(room_id))

    async def _create_relay_offer_async(self, room_id: str) -> dict:
        """异步创建中继 offer"""
        await self._create_room(room_id)
        room = self.rooms[room_id]

        # 创建一个 data channel 用于媒体协商
        pc = room['pc_caller']
        await pc.setLocalDescription(await pc.createOffer())
        return {
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }

    def relay_offer_from_caller(self, room_id: str, offer_sdp: str, offer_type: str = "offer") -> dict:
        """处理来自呼叫方的中继请求"""
        return self._run_async(self._handle_offer(room_id, offer_sdp, offer_type, True))

    def relay_offer_from_callee(self, room_id: str, offer_sdp: str, offer_type: str = "offer") -> dict:
        """处理来自被呼叫方的中继请求"""
        return self._run_async(self._handle_offer(room_id, offer_sdp, offer_type, False))

    def add_relay_candidate(self, room_id: str, candidate: dict, is_caller: bool):
        """添加中继 ICE candidate"""
        try:
            self._run_async(self._handle_ice_candidate(room_id, candidate, is_caller))
        except Exception as e:
            logger.warning(f"添加中继 candidate 失败: {e}")

    def handle_relay_answer(self, room_id: str, answer_sdp: str, answer_type: str, is_caller: bool):
        """处理中继模式的 answer (SDP answer，不是 ICE candidate)"""
        try:
            self._run_async(self._handle_relay_answer(room_id, answer_sdp, answer_type, is_caller))
        except Exception as e:
            logger.warning(f"处理中继 answer 失败: {e}")

    def close(self):
        """关闭中继服务器，清理所有资源"""
        if self.loop is not None:
            # 先关闭所有房间
            if self.rooms:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._close_all_rooms(),
                        self.loop
                    ).result(timeout=5)
                except Exception:
                    pass

            # 停止事件循环
            self.loop.call_soon_threadsafe(self.loop.stop)

            # 等待线程结束
            if self.thread is not None:
                self.thread.join(timeout=2)

            self.loop = None
            self.thread = None
            self._started = False
            logger.info("媒体中继服务已关闭")

    async def _close_all_rooms(self):
        """关闭所有房间"""
        room_ids = list(self.rooms.keys())
        for room_id in room_ids:
            await self._close_room(room_id)

    def close_relay_room(self, room_id: str):
        """关闭中继房间"""
        try:
            self._run_async(self._close_room(room_id))
        except Exception as e:
            logger.warning(f"关闭中继房间失败: {e}")


# 全局中继服务器实例
relay_server = MediaRelayServer()
