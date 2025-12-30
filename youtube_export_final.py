"""
YouTube Studio 批量导出工具 - 最终版
解决 YouTube 每次最多导出12条的限制
通过多次导出 + 滚动/翻页 + 合并去重
"""

import asyncio
import csv
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright, Page
except ImportError:
    print("正在安装 playwright...")
    os.system(f"{sys.executable} -m pip install playwright")
    os.system(f"{sys.executable} -m playwright install chromium")
    from playwright.async_api import async_playwright, Page


# ==================== 配置 ====================
CHROME_DEBUG_PORT = 9222
OUTPUT_DIR = "youtube_exports"
DOWNLOADS_DIR = os.path.join(OUTPUT_DIR, "downloads")
MAX_EXPORT_ROUNDS = 50  # 最多导出轮数，防止无限循环
# ==============================================


class YouTubeExporter:
    def __init__(self):
        self.page: Page = None
        self.playwright = None
        self.browser = None
        self.exported_count = 0
        
    async def connect(self) -> bool:
        """连接到已打开的 Chrome"""
        print("\n📌 连接 Chrome...")
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"http://localhost:{CHROME_DEBUG_PORT}"
            )
            
            contexts = self.browser.contexts
            if not contexts:
                return False
            
            for page in contexts[0].pages:
                if "studio.youtube.com" in page.url:
                    self.page = page
                    print(f"   ✅ 已连接: {page.url[:60]}...")
                    return True
            
            if contexts[0].pages:
                self.page = contexts[0].pages[0]
                return True
                
            return False
            
        except Exception as e:
            print(f"   ❌ 连接失败: {e}")
            return False
    
    async def goto_content_analytics(self):
        """导航到内容分析页面"""
        print("\n📌 导航到内容分析页面...")
        
        match = re.search(r'/channel/(UC[a-zA-Z0-9_-]+)', self.page.url)
        if match:
            channel_id = match.group(1)
            url = f"https://studio.youtube.com/channel/{channel_id}/analytics/tab-content/period-default"
            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            print("   ✅ 已到达内容分析页面")
    
    async def get_video_count(self) -> int:
        """获取页面上显示的视频总数"""
        # 尝试从页面上找到总数显示
        count = await self.page.evaluate("""
            () => {
                // 查找显示总数的元素，如 "1-12 / 56"
                const texts = document.body.innerText;
                const match = texts.match(/\\d+\\s*[-–]\\s*\\d+\\s*\\/\\s*(\\d+)/);
                if (match) return parseInt(match[1]);
                
                // 备选：计算表格行数
                const rows = document.querySelectorAll('ytcp-video-row, [class*="entity-row"]');
                return rows.length;
            }
        """)
        return count or 0
    
    async def scroll_table_down(self):
        """滚动表格区域，加载下一批数据"""
        await self.page.evaluate("""
            () => {
                // 找到表格容器并滚动
                const containers = [
                    document.querySelector('ytcp-entity-page'),
                    document.querySelector('.style-scope.ytcp-analytics-video-table'),
                    document.querySelector('main'),
                    document.documentElement
                ];
                for (const c of containers) {
                    if (c && c.scrollHeight > c.clientHeight) {
                        c.scrollTop = c.scrollHeight;
                    }
                }
                window.scrollTo(0, document.body.scrollHeight);
            }
        """)
        await asyncio.sleep(1)
    
    async def click_next_page(self) -> bool:
        """尝试点击下一页按钮"""
        next_btn = await self.page.query_selector(
            '[aria-label*="下一页"], [aria-label*="Next"], '
            'button:has-text("下一页"), button:has-text("Next"), '
            '[icon="chevron_right"], .pagination-next'
        )
        
        if next_btn:
            is_disabled = await next_btn.get_attribute("disabled")
            aria_disabled = await next_btn.get_attribute("aria-disabled")
            
            if not is_disabled and aria_disabled != "true":
                await next_btn.click()
                await asyncio.sleep(2)
                return True
        
        return False
    
    async def export_once(self) -> str:
        """执行一次导出，返回下载的文件路径"""
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        
        # 找导出按钮
        export_btn = await self.page.query_selector('[aria-label*="导出"], [aria-label*="Export"]')
        if not export_btn:
            export_btn = await self.page.evaluate_handle("""
                () => {
                    for (const btn of document.querySelectorAll('button, ytcp-button')) {
                        const text = (btn.textContent + (btn.getAttribute('aria-label') || '')).toLowerCase();
                        if (text.includes('导出') || text.includes('export')) return btn;
                    }
                    return null;
                }
            """)
        
        if not export_btn:
            return None
        
        # 点击导出按钮
        await export_btn.click()
        await asyncio.sleep(1)
        
        # 找 CSV 选项
        csv_option = await self.page.query_selector(
            '[role="menuitem"]:has-text("CSV"), '
            '[role="menuitem"]:has-text("导出当前视图"), '
            '[role="menuitem"]:has-text("Export current view")'
        )
        
        if not csv_option:
            csv_option = await self.page.evaluate_handle("""
                () => {
                    for (const item of document.querySelectorAll('[role="menuitem"], tp-yt-paper-item')) {
                        const text = item.textContent.toLowerCase();
                        if (text.includes('csv') || text.includes('导出') || text.includes('export')) {
                            return item;
                        }
                    }
                    return null;
                }
            """)
        
        if not csv_option:
            await self.page.keyboard.press("Escape")
            return None
        
        # 点击下载
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await csv_option.click()
            
            download = await download_info.value
            filename = download.suggested_filename
            filepath = os.path.join(DOWNLOADS_DIR, f"{datetime.now().strftime('%H%M%S')}_{filename}")
            await download.save_as(filepath)
            return filepath
            
        except Exception as e:
            print(f"   下载出错: {e}")
            return None
    
    async def export_all(self):
        """循环导出所有数据"""
        print("\n📌 开始批量导出...")
        
        total_videos = await self.get_video_count()
        print(f"   检测到约 {total_videos} 个视频")
        
        if total_videos > 12:
            print(f"   需要多次导出（每次最多12条）")
            estimated_rounds = (total_videos + 11) // 12
            print(f"   预计需要 {estimated_rounds} 轮导出")
        
        downloaded_files = []
        
        for round_num in range(1, MAX_EXPORT_ROUNDS + 1):
            print(f"\n   📥 第 {round_num} 轮导出...", end=" ")
            
            filepath = await self.export_once()
            
            if filepath:
                downloaded_files.append(filepath)
                self.exported_count += 1
                print(f"✅ 成功")
            else:
                print(f"❌ 失败")
                break
            
            # 尝试翻页或滚动到下一批
            has_next = await self.click_next_page()
            
            if not has_next:
                # 没有下一页按钮，尝试滚动
                await self.scroll_table_down()
                await asyncio.sleep(1)
                
                # 检查是否还有更多数据
                new_count = await self.get_video_count()
                if new_count <= total_videos and round_num * 12 >= total_videos:
                    print(f"\n   ✅ 已导出所有数据")
                    break
            
            await asyncio.sleep(1)
        
        print(f"\n   📊 共完成 {self.exported_count} 轮导出")
        return downloaded_files
    
    async def close(self):
        if self.playwright:
            await self.playwright.stop()


def extract_and_merge(download_dir: str = DOWNLOADS_DIR) -> str:
    """解压并合并所有下载的文件，去重"""
    print("\n📌 合并所有导出文件...")
    
    if not os.path.exists(download_dir):
        print("   没有下载文件")
        return None
    
    all_rows = []
    fieldnames = None
    file_count = 0
    
    for filename in sorted(os.listdir(download_dir)):
        filepath = os.path.join(download_dir, filename)
        
        if filename.endswith('.zip'):
            file_count += 1
            try:
                with zipfile.ZipFile(filepath, 'r') as zf:
                    for name in zf.namelist():
                        if '表格' in name or 'Table' in name:
                            with zf.open(name) as f:
                                content = f.read().decode('utf-8-sig')
                                lines = content.strip().split('\n')
                                reader = csv.DictReader(lines)
                                
                                if not fieldnames:
                                    fieldnames = reader.fieldnames
                                
                                for row in reader:
                                    first_val = list(row.values())[0] if row else ""
                                    if first_val.lower() not in ['total', '总计', '合计']:
                                        all_rows.append(dict(row))
            except Exception as e:
                print(f"   ⚠️ 处理 {filename} 出错: {e}")
        
        elif filename.endswith('.csv') and not filename.startswith('youtube_all'):
            file_count += 1
            try:
                with open(filepath, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    if not fieldnames:
                        fieldnames = reader.fieldnames
                    for row in reader:
                        first_val = list(row.values())[0] if row else ""
                        if first_val.lower() not in ['total', '总计', '合计']:
                            all_rows.append(dict(row))
            except Exception as e:
                print(f"   ⚠️ 处理 {filename} 出错: {e}")
    
    print(f"   处理了 {file_count} 个文件，共 {len(all_rows)} 行原始数据")
    
    if not all_rows or not fieldnames:
        print("   没有数据可合并")
        return None
    
    # 去重
    seen = set()
    unique_rows = []
    
    # 找到用于去重的列（视频标题或 ID）
    id_col = None
    for col in ['Content', '内容', 'Video title', '视频标题', 'video_id']:
        if col in fieldnames:
            id_col = col
            break
    
    for row in all_rows:
        if id_col:
            key = row.get(id_col, '')
        else:
            key = str(sorted(row.items()))
        
        if key and key not in seen:
            seen.add(key)
            unique_rows.append(row)
    
    # 保存
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"youtube_all_videos_{timestamp}.csv")
    
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unique_rows)
    
    print(f"\n   ✅ 合并完成!")
    print(f"   📁 文件: {output_path}")
    print(f"   📊 共 {len(unique_rows)} 条记录（去重后）")
    
    return output_path


async def main():
    print("\n" + "=" * 60)
    print("   📊 YouTube Studio 批量导出工具")
    print("   解决每次最多导出12条的限制")
    print("=" * 60)
    
    exporter = YouTubeExporter()
    
    try:
        if not await exporter.connect():
            print("\n❌ 无法连接 Chrome")
            print("   1. 运行 start_chrome.bat 启动 Chrome")
            print("   2. 登录 YouTube Studio")
            print("   3. 重新运行此脚本")
            return
        
        await exporter.goto_content_analytics()
        
        print("\n" + "-" * 60)
        print("📋 请在 Chrome 中:")
        print("   1. 确认已在 '分析 > 内容' 页面")
        print("   2. 设置好时间范围")
        print("   3. 脚本会自动多次导出并合并")
        print("-" * 60)
        input("\n准备好后按 Enter 开始...")
        
        # 批量导出
        await exporter.export_all()
        
        # 合并所有文件
        extract_and_merge()
    
    finally:
        await exporter.close()
    
    print("\n" + "=" * 60)
    print("   ✅ 完成!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
