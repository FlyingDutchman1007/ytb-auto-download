# YouTube Studio 批量导出工具

解决 YouTube Studio **每次只能导出12条**的限制，自动多次导出并合并。

## 📋 前提条件

- Windows 系统
- 已安装 [Python 3.9+](https://www.python.org/downloads/)
- 已安装 Google Chrome

## 🚀 使用步骤

### 首次使用
```
1. 双击 setup.bat      → 安装依赖（只需一次）
2. 双击 start_chrome.bat → 启动专用 Chrome
3. 在 Chrome 中登录 YouTube Studio，进入 分析 > 内容
4. 双击 run_scraper.bat   → 自动批量导出
```

### 之后使用
```
1. 双击 start_chrome.bat → 启动 Chrome（已保存登录）
2. 进入 分析 > 内容 页面
3. 双击 run_scraper.bat   → 导出
```

## 📁 需要的文件

分享给同事时，发送这些文件：
```
youtube_tool/
├── setup.bat              # 安装依赖
├── start_chrome.bat       # 启动 Chrome
├── run_scraper.bat        # 运行脚本
├── youtube_export_final.py # 主程序
└── README.md              # 说明文档
```

**不要发送**：`.venv/`、`chrome_debug_profile/`、`youtube_exports/`

## 📤 输出

导出的数据在 `youtube_exports/youtube_all_videos_时间戳.csv`
