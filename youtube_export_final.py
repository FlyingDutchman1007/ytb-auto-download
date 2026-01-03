"""
YouTube Studio 批量导出工具
解决 YouTube 每次最多只能勾选 12 个视频导出的限制

使用方法：
1. 运行 start_chrome.bat 启动 Chrome
2. 打开 YouTube Studio > 分析 > 内容 > 高级模式
3. 设置好时间范围和筛选条件
4. 运行此脚本

导出逻辑：
- 每次勾选最多 12 个视频 → 导出 → 取消勾选 → 滚动 → 重复
- Table data: 用第一次（包含所有视频汇总）
- Chart data: 拼接所有（每批视频的详细数据）
- Totals: 用第一次
"""

import asyncio
import csv
import os
import sys
import zipfile
from datetime import datetime

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
MAX_VIDEOS_PER_EXPORT = 6  # 每次6个，更好监控
MAX_EXPORT_ROUNDS = 100
# ==============================================


def get_videos_from_zip(filepath: str) -> list:
    """从 ZIP 文件中读取 Chart data，返回视频名字列表"""
    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            for name in zf.namelist():
                if 'Chart' in name or 'chart' in name:
                    with zf.open(name) as f:
                        content = f.read().decode('utf-8-sig')
                        reader = csv.DictReader(content.strip().split('\n'))
                        # 找到视频标题列
                        video_titles = set()
                        for row in reader:
                            # 尝试不同的列名
                            for col in ['视频标题', 'Video title', '视频', 'Video', 'Content']:
                                if col in row and row[col]:
                                    video_titles.add(row[col])
                                    break
                        return list(video_titles)
    except Exception as e:
        print(f"      读取 ZIP 失败: {e}")
    return []


class YouTubeExporter:
    def __init__(self):
        self.page: Page = None
        self.playwright = None
        self.browser = None
        self.exported_count = 0
        self.exported_videos = set()  # 记录已导出的视频（用文本标识）
        
    async def connect(self) -> bool:
        """连接到已打开的 Chrome"""
        print("\n📌 连接 Chrome...")
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"http://localhost:{CHROME_DEBUG_PORT}"
            )
            
            contexts = self.browser.contexts
            if not contexts or not contexts[0].pages:
                print("   ❌ 没有找到打开的页面")
                return False
            
            # 找 YouTube Studio 页面
            for page in contexts[0].pages:
                if "studio.youtube.com" in page.url:
                    self.page = page
                    print(f"   ✅ 已连接: {page.url[:70]}...")
                    return True
            
            # 用第一个页面
            self.page = contexts[0].pages[0]
            print(f"   ✅ 已连接: {self.page.url[:70]}...")
            return True
            
        except Exception as e:
            print(f"   ❌ 连接失败: {e}")
            print("   请确保已运行 start_chrome.bat")
            return False
    
    async def get_video_checkboxes(self) -> list:
        """获取所有视频的复选框，返回包含索引的信息"""
        checkboxes = await self.page.evaluate(r'''() => {
            const results = [];
            
            // 找 role=checkbox 的元素
            const allCheckboxes = document.querySelectorAll("[role='checkbox']");
            
            let index = 0;
            for (const cb of allCheckboxes) {
                const rect = cb.getBoundingClientRect();
                
                // 跳过不可见的
                if (rect.width === 0 || rect.height === 0) continue;
                
                // 向上找包含文本的父元素
                let row = cb;
                let text = "";
                for (let i = 0; i < 10 && row; i++) {
                    row = row.parentElement;
                    if (row && row.innerText && row.innerText.length > 10) {
                        text = row.innerText;
                        break;
                    }
                }
                
                // 跳过"合计"行
                if (text.includes("合计") || text.includes("Total") || text.includes("总计")) {
                    index++;
                    continue;
                }
                
                // 跳过没有视频信息的行（视频行会有时长如 2:31）
                if (!text.match(/\d:\d\d/)) {
                    index++;
                    continue;
                }
                
                // 检查是否选中
                const isChecked = cb.getAttribute("aria-checked") === "true";
                
                results.push({
                    index: index,
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                    checked: isChecked,
                    text: text.substring(0, 50).replace(/\n/g, " ")
                });
                index++;
            }
            
            return results;
        }''')
        return checkboxes or []
    
    async def click_checkbox_by_text(self, target_text: str) -> bool:
        """用 JavaScript 直接点击包含指定文本的 checkbox"""
        result = await self.page.evaluate(r'''(targetText) => {
            const allCheckboxes = document.querySelectorAll("[role='checkbox']");
            
            for (const cb of allCheckboxes) {
                // 找父行
                let row = cb;
                let text = "";
                for (let i = 0; i < 10 && row; i++) {
                    row = row.parentElement;
                    if (row && row.innerText && row.innerText.length > 10) {
                        text = row.innerText;
                        break;
                    }
                }
                
                // 匹配文本（前30字符）
                if (text.substring(0, 30) === targetText.substring(0, 30)) {
                    // 直接点击 checkbox
                    cb.click();
                    // 返回点击后的状态
                    return {
                        success: true,
                        newState: cb.getAttribute("aria-checked") === "true"
                    };
                }
            }
            return { success: false };
        }''', target_text)
        return result.get('success', False), result.get('newState', False)
    
    async def count_checked(self) -> int:
        """计算当前勾选的视频数量"""
        checkboxes = await self.get_video_checkboxes()
        return sum(1 for cb in checkboxes if cb['checked'])
    
    async def select_videos(self, max_count: int = 12) -> int:
        """勾选视频复选框，返回勾选数量"""
        checkboxes = await self.get_video_checkboxes()
        
        if not checkboxes:
            print("   ⚠️ 未找到视频复选框")
            return 0
        
        print(f"   📋 找到 {len(checkboxes)} 个可见视频")
        
        # 筛选未勾选的
        unchecked = [cb for cb in checkboxes if not cb['checked']]
        
        if not unchecked:
            print("   ℹ️ 当前可见视频都已勾选")
            return 0
        
        # 勾选前 max_count 个
        to_select = unchecked[:max_count]
        selected_count = 0
        
        for cb in to_select:
            try:
                await self.page.mouse.click(cb['x'], cb['y'])
                await asyncio.sleep(0.3)
                selected_count += 1
            except Exception as e:
                print(f"   ⚠️ 勾选失败: {e}")
        
        # 验证勾选结果
        await asyncio.sleep(0.5)
        actual_checked = await self.count_checked()
        print(f"   ✅ 当前已勾选: {actual_checked} 个视频")
        
        return selected_count
    
    async def unselect_all(self):
        """取消所有勾选 - 使用 Playwright locator"""
        for attempt in range(3):
            checkboxes = self.page.locator("[role='checkbox'][aria-checked='true']")
            count = await checkboxes.count()
            
            if count == 0:
                break
            
            print(f"      取消 {count} 个勾选...")
            for i in range(count):
                try:
                    cb = checkboxes.nth(i)
                    if await cb.is_visible():
                        await cb.click()
                        await asyncio.sleep(0.2)
                except:
                    pass
            
            await asyncio.sleep(0.3)
    
    async def scroll_down_once(self) -> int:
        """向下滚动一次，返回当前视频数量"""
        await self.page.evaluate("""
            () => {
                // 找表格容器并滚动
                const scrollables = document.querySelectorAll(
                    '[class*="table-body"], [class*="scroll"], ' +
                    '[style*="overflow"], main, [class*="content"]'
                );
                for (const el of scrollables) {
                    if (el.scrollHeight > el.clientHeight) {
                        el.scrollBy(0, 500);
                    }
                }
                window.scrollBy(0, 500);
            }
        """)
        await asyncio.sleep(1)
        checkboxes = await self.get_video_checkboxes()
        return len(checkboxes)
    
    async def scroll_to_top(self):
        """滚动回顶部"""
        await self.page.evaluate("""
            () => {
                const scrollables = document.querySelectorAll(
                    '[class*="table-body"], [class*="scroll"], ' +
                    '[style*="overflow"], main, [class*="content"]'
                );
                for (const el of scrollables) {
                    if (el.scrollHeight > el.clientHeight) {
                        el.scrollTop = 0;
                    }
                }
                window.scrollTo(0, 0);
            }
        """)
        await asyncio.sleep(0.5)
    
    async def load_all_videos(self) -> list:
        """
        持续滚动直到加载所有视频，返回所有视频的信息列表
        这是更稳健的方法：先加载全部，再处理
        """
        print("   📜 加载所有视频（滚动到底）...")
        
        all_videos = {}  # 用 text 作为 key 去重
        no_new_count = 0
        max_scrolls = 30  # 最多滚动30次
        
        for scroll_num in range(max_scrolls):
            checkboxes = await self.get_video_checkboxes()
            
            # 统计新发现的视频
            new_found = 0
            for cb in checkboxes:
                video_id = cb['text'].strip()[:50]
                if video_id and video_id not in all_videos:
                    all_videos[video_id] = cb
                    new_found += 1
            
            print(f"      滚动 {scroll_num + 1}: 可见 {len(checkboxes)} 个, 累计发现 {len(all_videos)} 个视频", end="\r")
            
            if new_found == 0:
                no_new_count += 1
                if no_new_count >= 3:  # 连续3次没有新视频，认为到底了
                    break
            else:
                no_new_count = 0
            
            await self.scroll_down_once()
        
        print(f"\n   ✅ 共发现 {len(all_videos)} 个视频")
        
        # 滚动回顶部
        await self.scroll_to_top()
        
        return list(all_videos.values())
    
    async def click_export_button(self) -> bool:
        """点击导出按钮"""
        export_btn = await self.page.evaluate_handle("""
            () => {
                // 方法1: aria-label 包含导出
                const labels = ['导出当前视图', 'Export current view', '导出', 'Export'];
                for (const label of labels) {
                    const btns = document.querySelectorAll(`[aria-label*="${label}"]`);
                    for (const btn of btns) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return btn;
                        }
                    }
                }
                
                // 方法2: 下载图标按钮
                const downloadBtns = document.querySelectorAll('[icon*="download"], [icon*="export"]');
                for (const btn of downloadBtns) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return btn;
                    }
                }
                
                return null;
            }
        """)
        
        if not export_btn:
            print("   ❌ 未找到导出按钮")
            return False
        
        await export_btn.click()
        await asyncio.sleep(1)
        return True
    
    async def click_csv_option(self) -> bool:
        """点击 CSV 下载选项"""
        await asyncio.sleep(0.5)
        
        csv_option = await self.page.evaluate_handle("""
            () => {
                // 查找菜单项
                const items = document.querySelectorAll(
                    '[role="menuitem"], tp-yt-paper-item, paper-item, ' +
                    '[class*="menu-item"], [class*="dropdown-item"]'
                );
                for (const item of items) {
                    const text = (item.textContent || '').toLowerCase();
                    const rect = item.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        if (text.includes('csv') || 
                            text.includes('导出当前视图') || 
                            text.includes('export current view')) {
                            return item;
                        }
                    }
                }
                return null;
            }
        """)
        
        if not csv_option:
            await self.page.keyboard.press("Escape")
            print("   ❌ 未找到 CSV 选项")
            return False
        
        await csv_option.click()
        return True
    
    async def export_once(self) -> str:
        """执行一次导出，返回文件路径"""
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        
        if not await self.click_export_button():
            return None
        
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                if not await self.click_csv_option():
                    return None
            
            download = await download_info.value
            filename = download.suggested_filename
            filepath = os.path.join(DOWNLOADS_DIR, f"{self.exported_count:03d}_{filename}")
            await download.save_as(filepath)
            self.exported_count += 1
            return filepath
            
        except Exception as e:
            print(f"   ❌ 下载失败: {e}")
            await self.page.keyboard.press("Escape")
            return None
    
    async def select_first_n_unchecked(self, n: int = 6) -> tuple:
        """
        用 Playwright locator 点击前 N 个未勾选的视频 checkbox
        返回 (成功数量, 视频标题列表)
        """
        # 找所有 checkbox
        checkboxes = self.page.locator("[role='checkbox']")
        count = await checkboxes.count()
        
        selected = []
        selected_count = 0
        
        for i in range(count):
            if selected_count >= n:
                break
            
            cb = checkboxes.nth(i)
            
            try:
                # 检查是否可见
                if not await cb.is_visible():
                    continue
                
                # 获取父行文本
                parent = cb.locator("xpath=ancestor::*[string-length(normalize-space()) > 20][1]")
                text = ""
                try:
                    text = await parent.inner_text(timeout=500)
                except:
                    pass
                
                # 跳过合计行
                if "合计" in text or "Total" in text:
                    continue
                
                # 必须是视频行（有时长）
                import re
                if not re.search(r'\d:\d\d', text):
                    continue
                
                # 检查是否已勾选
                checked = await cb.get_attribute("aria-checked")
                if checked == "true":
                    continue
                
                # 用 Playwright 点击（最可靠的方式）
                await cb.click()
                await asyncio.sleep(0.3)
                
                # 验证点击后状态
                new_checked = await cb.get_attribute("aria-checked")
                if new_checked == "true":
                    selected_count += 1
                    title = text.split('\n')[0][:40] if text else f"视频{i}"
                    selected.append(title)
                    print(f"      ✓ [{selected_count}] {title}")
                else:
                    print(f"      ✗ 点击无效: {text[:30]}")
                    
            except Exception as e:
                continue
        
        return selected_count, selected

    async def export_all(self) -> list:
        """
        批量导出所有视频 - 简单直接版
        
        策略：每轮直接在当前页面勾选12个，导出，滚动，重复
        """
        print("\n" + "=" * 55)
        print("   📊 开始批量导出")
        print("=" * 55)
        
        downloaded_files = []
        exported_video_titles = set()  # 用标题判重
        round_num = 0
        
        while round_num < MAX_EXPORT_ROUNDS:
            round_num += 1
            print(f"\n{'─' * 55}")
            print(f"📥 第 {round_num} 轮")
            print(f"{'─' * 55}")
            
            # 1. 取消所有勾选
            print("   🔄 取消所有勾选...")
            await self.unselect_all()
            await asyncio.sleep(0.3)
            
            # 2. 直接用 JS 勾选前12个未勾选视频
            print("   ☑️ 勾选视频...")
            count, videos = await self.select_first_n_unchecked(MAX_VIDEOS_PER_EXPORT)
            
            print(f"   ✅ 成功勾选 {count} 个视频:")
            for v in videos:
                print(f"      - {v[:45]}")
            
            if count == 0:
                # 尝试滚动找更多
                print("   📜 滚动查找更多...")
                for _ in range(3):
                    await self.scroll_down_once()
                    count, videos = await self.select_first_n_unchecked(MAX_VIDEOS_PER_EXPORT)
                    if count > 0:
                        break
                
                if count == 0:
                    print("   ✅ 所有视频都已导出完成！")
                    break
            
            # 3. 导出
            print("   📤 导出...")
            filepath = await self.export_once()
            
            if filepath:
                downloaded_files.append(filepath)
                print(f"   ✅ 下载: {os.path.basename(filepath)}")
                
                # 检查 ZIP 里的视频
                actual_videos = get_videos_from_zip(filepath)
                print(f"   📋 ZIP 包含 {len(actual_videos)} 个视频:")
                for v in actual_videos:
                    print(f"      - {v[:45]}")
                    exported_video_titles.add(v)
                
                print(f"   📊 累计导出 {len(exported_video_titles)} 个不同视频")
            else:
                print("   ❌ 导出失败")
            
            # 4. 滚动，准备下一轮
            await self.scroll_down_once()
            await asyncio.sleep(0.5)
        
        print(f"\n{'=' * 55}")
        print(f"   📊 完成！共 {len(downloaded_files)} 个文件")
        print(f"   📊 累计 {len(exported_video_titles)} 个不同视频")
        print(f"{'=' * 55}")
        
        return downloaded_files
        
        return downloaded_files
    
    async def close(self):
        if self.playwright:
            await self.playwright.stop()


def merge_exports(download_dir: str = DOWNLOADS_DIR) -> dict:
    """
    合并导出文件
    - Table data: 用第一个（已包含所有视频汇总）
    - Totals: 用第一个  
    - Chart data: 拼接所有（每批视频的详细时间序列数据）
    """
    print("\n📌 合并导出文件...")
    
    if not os.path.exists(download_dir):
        print("   没有下载文件")
        return None
    
    table_data = None
    totals_data = None
    chart_data_rows = []
    chart_fieldnames = None
    all_videos_in_charts = {}  # 记录每个文件包含的视频
    
    zip_files = sorted([f for f in os.listdir(download_dir) if f.endswith('.zip')])
    
    if not zip_files:
        print("   没有找到 ZIP 文件")
        return None
    
    print(f"   找到 {len(zip_files)} 个 ZIP 文件")
    
    for i, filename in enumerate(zip_files):
        filepath = os.path.join(download_dir, filename)
        is_first = (i == 0)
        videos_in_this_file = set()
        
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
                                # 记录视频名
                                for col in ['视频标题', 'Video title', '视频', 'Video', 'Content']:
                                    if col in row and row[col]:
                                        videos_in_this_file.add(row[col])
                                        break
                            
                            print(f"\n   📊 ZIP #{i+1}: {filename}")
                            print(f"      Chart data: {row_count} 行")
                            print(f"      包含视频 ({len(videos_in_this_file)} 个):")
                            for v in list(videos_in_this_file)[:8]:
                                print(f"        - {v[:50]}")
                            if len(videos_in_this_file) > 8:
                                print(f"        ... 还有 {len(videos_in_this_file) - 8} 个")
                            
                            all_videos_in_charts[filename] = videos_in_this_file
                            
        except Exception as e:
            print(f"   ⚠️ 处理 {filename} 出错: {e}")
    
    # 检查重复
    print(f"\n   📋 重复检查:")
    all_unique_videos = set()
    for fname, videos in all_videos_in_charts.items():
        overlap = all_unique_videos & videos
        if overlap:
            print(f"      ⚠️ {fname} 有 {len(overlap)} 个重复视频")
        all_unique_videos.update(videos)
    print(f"      总计去重后: {len(all_unique_videos)} 个不同视频")
    
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
    print("\n" + "=" * 55)
    print("   📊 YouTube Studio 批量导出工具")
    print("   解决每次最多勾选 12 个视频的限制")
    print("=" * 55)
    
    exporter = YouTubeExporter()
    
    try:
        if not await exporter.connect():
            print("\n❌ 无法连接 Chrome")
            print("   1. 运行 start_chrome.bat 启动 Chrome")
            print("   2. 打开 YouTube Studio")
            print("   3. 进入 分析 > 内容 > 高级模式")
            print("   4. 设置好时间范围和筛选条件")
            print("   5. 重新运行此脚本")
            return
        
        print("\n" + "-" * 55)
        print("📋 请确认：")
        print("   1. 已在 YouTube Studio 高级模式")
        print("   2. 已设置好时间范围和筛选条件")
        print("   3. 可以看到视频列表和前面的复选框")
        print("-" * 55)
        input("\n准备好后按 Enter 开始...")
        
        # 清空旧的下载
        if os.path.exists(DOWNLOADS_DIR):
            for f in os.listdir(DOWNLOADS_DIR):
                try:
                    os.remove(os.path.join(DOWNLOADS_DIR, f))
                except:
                    pass
        
        # 批量导出
        await exporter.export_all()
        
        # 合并文件
        merge_exports()
    
    finally:
        await exporter.close()
    
    print("\n" + "=" * 55)
    print("   ✅ 完成!")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
