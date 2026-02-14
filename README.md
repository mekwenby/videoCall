# 视频通话系统

基于 WebRTC 和 Flask-SocketIO 的视频通话应用，支持 P2P 连接和服务器信令中转。
# 在线Demo
https://call.030399.xyz/
## 功能特性

- 📹 一对一视频通话
- 🎤 语音通话支持
- 📱 移动端适配
- 🔔 来电提醒
- 👥 在线用户列表
- 🔇 静音/取消静音
- 🎥 开启/关闭摄像头

## 技术栈

- **后端**: Flask + Flask-SocketIO
- **前端**: HTML5 + JavaScript + Socket.IO Client
- **实时通信**: WebRTC + Socket.IO
- **异步模式**: Eventlet

## 项目结构

```
video call/
├── app_relay.py              # Flask 服务器主程序
├── requirements.txt          # Python 依赖
├── templates/
│   └── index_relay.html      # 前端页面
├── README.md                 # 项目说明文档
└── 接口文档.md               # 接口文档
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务器

```bash
python app_relay.py
```

### 3. 访问应用

打开浏览器访问: http://localhost:5000

## 使用说明

1. **进入页面**: 系统自动分配用户名（如 Call_ab12）
2. **查看在线用户**: 在首页查看当前在线的其他用户
3. **发起通话**: 点击用户右侧的"发起通话"按钮
4. **接听来电**: 收到来电时点击"接听"按钮
5. **拒绝来电**: 收到来电时点击"拒绝"按钮
6. **挂断通话**: 通话中点击红色挂断按钮
7. **媒体控制**: 通话中可切换静音和摄像头开关

## 系统要求

- Python 3.8+
- 现代浏览器（支持 WebRTC）
- 摄像头和麦克风权限

## 浏览器兼容性

- Chrome 60+
- Firefox 60+
- Safari 14+
- Edge 79+

## 注意事项

- 首次使用需要授权摄像头和麦克风权限
- 建议使用 HTTPS 部署以获得最佳兼容性
- 移动端使用时请允许横屏以获得更好体验
