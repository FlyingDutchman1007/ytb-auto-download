"""
YouTube Studio 批量导出工具 - 最终版
解决 YouTube 每次只能导出12个视频的 Chart Data 限制

导出的 ZIP 包含：
- Table data.csv  → 全部视频（用第一次）
- Chart data.csv  → 只有当前12个视频（需要拼接）
- Totals.csv      → 总计（用第一次）
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
    from playwright.async_api import async_playwright, Page


# ==================== 配置 ====================
CHROME_DEBUG_PORT = 9222
OUTPUT_DIR = "youtube_exports"
DOWNLOADS_DIR = os.path.join(OUTPUT_DIR, "downloads")
MAX_EXPORT_ROUNDS = 50
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
        """获取视频总数 - 从页面上的 '1-12 / 56' 或 '1–12 / 56' 格式提取"""
        result = await self.page.evaluate("""
            () => {
                // 多种可能的格式:
                // 中文: "1-12 / 56", "1–12 / 56" (en-dash)
                // 英文: "1-12 of 56", "1–12 of 56"
                // 可能有空格变化
                
                const patterns = [
                    /(\d+)\s*[-–]\s*(\d+)\s*\/\s*(\d+)/,      // "1-12 / 56"
                    /(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)/i,     // "1-12 of 56"
                    /(\d+)\s*[-–]\s*(\d+)\s*共\s*(\d+)/,      // "1-12 共 56"
                    /共\s*(\d+)\s*个/,                         // "共 56 个"
                    /(\d+)\s+videos?/i,                        // "56 videos"
                ];
                
                const texts = document.body.innerText;
                
                for (const pattern of patterns) {
                    const match = texts.match(pattern);
                    if (match) {
                        // 返回最后一个捕获组（总数）
                        const total = match[match.length - 1];
                        const num = parseInt(total);
                        if (num > 0 && num < 100000) {
                            console.log('Found video count:', num, 'with pattern:', pattern.toString());
                            return { count: num, pattern: pattern.toString(), matched: match[0] };
                        }
                    }
                }
                
                // 备选：尝试从分页区域查找
                const paginationEl = document.querySelector(
                    '[class*="pagination"], [class*="page-info"], ' +
                    'ytcp-table-footer, .table-footer, [class*="entity-page"]'
                );
                if (paginationEl) {
                    const pText = paginationEl.innerText;
                    for (const pattern of patterns) {
                        const match = pText.match(pattern);
                        if (match) {
                            const total = match[match.length - 1];
                            const num = parseInt(total);
                            if (num > 0) {
                                return { count: num, pattern: 'pagination-' + pattern.toString(), matched: match[0] };
                            }
                        }
                    }
                }
                
                return { count: 0, pattern: 'none', matched: '' };
            }
        """)
        
        if result and result.get('count', 0) > 0:
            print(f"   📊 检测到视频数量: {result['count']} (匹配: '{result.get('matched', '')}')")
            return result['count']
        else:
            print(f"   ⚠️ 未能自动检测视频数量，将持续导出直到没有下一页")
            return 0
    
    async def click_next_page(self) -> bool:
        """点击下一页"""
        next_btn = await self.page.query_selector(
            '[aria-label*="下一页"], [aria-label*="Next page"], '
            '[aria-label*="next"], [icon="chevron_right"]'
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
        """执行一次导出"""
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
        
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await csv_option.click()
            
            download = await download_info.value
            filename = download.suggested_filename
            filepath = os.path.join(DOWNLOADS_DIR, f"{self.exported_count:03d}_{filename}")
            await download.save_as(filepath)
            self.exported_count += 1
            return filepath
            
        except Exception as e:
            print(f"   下载出错: {e}")
            return None
    
    async def export_all(self) -> list:
        """循环导出所有数据"""
        print("\n📌 开始批量导出...")
        
        total_videos = await self.get_video_count()
        print(f"   检测到 {total_videos} 个视频")
        
        if total_videos > 12:
            estimated_rounds = (total_videos + 11) // 12
            print(f"   需要 {estimated_rounds} 轮导出 Chart Data")
        
        downloaded_files = []
        
        for round_num in range(1, MAX_EXPORT_ROUNDS + 1):
            print(f"\n   📥 第 {round_num} 轮导出...", end=" ")
            
            filepath = await self.export_once()
            
            if filepath:
                downloaded_files.append(filepath)
                print(f"✅")
            else:
                print(f"❌ 失败")
                break
            
            # 翻页
            has_next = await self.click_next_page()
            
            if not has_next:
                print(f"\n   ✅ 没有更多页了")
                break
            
            await asyncio.sleep(1)
        
        print(f"\n   📊 共完成 {len(downloaded_files)} 轮导出")
        return downloaded_files
    
    async def close(self):
        if self.playwright:
            await self.playwright.stop()


def merge_exports(download_dir: str = DOWNLOADS_DIR) -> dict:
    """
    合并导出文件
    - Table data: 用第一个
    - Totals: 用第一个  
    - Chart data: 拼接所有
    """
    print("\n📌 合并导出文件...")
    
    if not os.path.exists(download_dir):
        print("   没有下载文件")
        return None
    
    table_data = None
    totals_data = None
    chart_data_rows = []
    chart_fieldnames = None
    
    zip_files = sorted([f for f in os.listdir(download_dir) if f.endswith('.zip')])
    
    if not zip_files:
        print("   没有找到 ZIP 文件")
        return None
    
    print(f"   找到 {len(zip_files)} 个 ZIP 文件")
    
    for i, filename in enumerate(zip_files):
        filepath = os.path.join(download_dir, filename)
        is_first = (i == 0)
        
        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                for name in zf.namelist():
                    with zf.open(name) as f:
                        content = f.read().decode('utf-8-sig')
                        lines = content.strip().split('\n')
                        
                        # Table data - 只用第一个
                        if ('表格' in name or 'Table' in name) and is_first:
                            table_data = content
                            print(f"   ✅ Table data（来自第1个ZIP）")
                        
                        # Totals - 只用第一个
                        elif ('总计' in name or 'Totals' in name) and is_first:
                            totals_data = content
                            print(f"   ✅ Totals（来自第1个ZIP）")
                        
                        # Chart data - 拼接所有
                        elif '图表' in name or 'Chart' in name:
                            reader = csv.DictReader(lines)
                            if not chart_fieldnames:
                                chart_fieldnames = reader.fieldnames
                            
                            row_count = 0
                            for row in reader:
                                chart_data_rows.append(dict(row))
                                row_count += 1
                            
                            print(f"   📊 Chart data 第{i+1}批: +{row_count} 行")
                            
        except Exception as e:
            print(f"   ⚠️ 处理 {filename} 出错: {e}")
    
    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_subdir = os.path.join(OUTPUT_DIR, f"merged_{timestamp}")
    os.makedirs(output_subdir, exist_ok=True)
    
    result = {}
    
    # 保存 Table data
    if table_data:
        path = os.path.join(output_subdir, "Table data.csv")
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(table_data)
        result['table'] = path
    
    # 保存 Totals
    if totals_data:
        path = os.path.join(output_subdir, "Totals.csv")
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(totals_data)
        result['totals'] = path
    
    # 保存合并后的 Chart data
    if chart_data_rows and chart_fieldnames:
        path = os.path.join(output_subdir, "Chart data.csv")
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=chart_fieldnames)
            writer.writeheader()
            writer.writerows(chart_data_rows)
        result['chart'] = path
    
    print(f"\n   ✅ 合并完成!")
    print(f"   📁 输出目录: {output_subdir}")
    print(f"   📊 Chart data 共 {len(chart_data_rows)} 行")
    
    return result


async def main():
    print("\n" + "=" * 60)
    print("   📊 YouTube Studio 批量导出工具")
    print("   解决 Chart Data 每次只能导出12个视频的限制")
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
        print("   3. 确保视频列表从第1页开始")
        print("-" * 60)
        input("\n准备好后按 Enter 开始...")
        
        # 批量导出
        await exporter.export_all()
        
        # 合并文件
        merge_exports()
    
    finally:
        await exporter.close()
    
    print("\n" + "=" * 60)
    print("   ✅ 完成!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
