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
MAX_VIDEOS_PER_EXPORT = 12
MAX_EXPORT_ROUNDS = 100
# ==============================================


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
        """获取所有视频的复选框"""
        checkboxes = await self.page.evaluate(r'''() => {
            const results = [];
            
            // 找 role=checkbox 的元素
            const allCheckboxes = document.querySelectorAll("[role='checkbox']");
            
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
                    continue;
                }
                
                // 跳过没有视频信息的行（视频行会有时长如 2:31）
                if (!text.match(/\d:\d\d/)) {
                    continue;
                }
                
                // 检查是否选中
                const isChecked = cb.getAttribute("aria-checked") === "true";
                
                results.push({
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                    checked: isChecked,
                    text: text.substring(0, 50).replace(/\n/g, " ")
                });
            }
            
            return results;
        }''')
        return checkboxes or []
    
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
        """取消所有勾选"""
        for _ in range(3):  # 最多尝试3轮
            checkboxes = await self.get_video_checkboxes()
            checked = [cb for cb in checkboxes if cb['checked']]
            
            if not checked:
                break
            
            for cb in checked:
                try:
                    await self.page.mouse.click(cb['x'], cb['y'])
                    await asyncio.sleep(0.2)
                except:
                    pass
            
            await asyncio.sleep(0.3)
    
    async def scroll_down(self) -> bool:
        """向下滚动表格区域，返回是否有新内容"""
        old_checkboxes = await self.get_video_checkboxes()
        old_count = len(old_checkboxes)
        
        await self.page.evaluate("""
            () => {
                // 找表格容器并滚动
                const scrollables = document.querySelectorAll(
                    '[class*="table-body"], [class*="scroll"], ' +
                    '[style*="overflow"], main, [class*="content"]'
                );
                for (const el of scrollables) {
                    if (el.scrollHeight > el.clientHeight) {
                        el.scrollBy(0, 400);
                    }
                }
                window.scrollBy(0, 400);
            }
        """)
        await asyncio.sleep(1.5)
        
        new_checkboxes = await self.get_video_checkboxes()
        new_count = len(new_checkboxes)
        
        return new_count != old_count
    
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
    
    async def export_all(self) -> list:
        """批量导出所有视频"""
        print("\n" + "=" * 55)
        print("   📊 开始批量导出")
        print("=" * 55)
        
        downloaded_files = []
        round_num = 0
        no_progress_count = 0
        
        while round_num < MAX_EXPORT_ROUNDS:
            round_num += 1
            print(f"\n{'─' * 55}")
            print(f"📥 第 {round_num} 轮")
            print(f"{'─' * 55}")
            
            # 1. 先取消所有勾选
            print("   🔄 取消已有勾选...")
            await self.unselect_all()
            await asyncio.sleep(0.5)
            
            # 2. 获取当前可见的视频复选框
            checkboxes = await self.get_video_checkboxes()
            print(f"   📋 当前可见 {len(checkboxes)} 个视频")
            
            # 3. 筛选出未导出过的视频（用文本标识判断）
            not_exported = []
            for cb in checkboxes:
                video_id = cb['text'].strip()[:30]  # 用前30字符作为标识
                if video_id and video_id not in self.exported_videos:
                    not_exported.append(cb)
            
            print(f"   📋 其中 {len(not_exported)} 个未导出")
            
            if not not_exported:
                # 尝试滚动加载更多
                print("   📜 滚动查找更多视频...")
                await self.scroll_down()
                await asyncio.sleep(1)
                
                checkboxes = await self.get_video_checkboxes()
                not_exported = []
                for cb in checkboxes:
                    video_id = cb['text'].strip()[:30]
                    if video_id and video_id not in self.exported_videos:
                        not_exported.append(cb)
                
            if not not_exported:
                # 滚动后还是没有新视频，直接结束
                print("\n   ✅ 所有视频都已导出完成！")
                break
            
            no_progress_count = 0
            
            # 4. 勾选这批视频（最多12个）
            to_select = not_exported[:MAX_VIDEOS_PER_EXPORT]
            selected_count = 0
            selected_ids = []
            
            print(f"   ☑️ 勾选 {len(to_select)} 个视频...")
            for cb in to_select:
                try:
                    await self.page.mouse.click(cb['x'], cb['y'])
                    await asyncio.sleep(0.3)
                    selected_count += 1
                    selected_ids.append(cb['text'].strip()[:30])
                except Exception as e:
                    print(f"   ⚠️ 勾选失败: {e}")
            
            print(f"   ✅ 已勾选 {selected_count} 个视频")
            
            if selected_count == 0:
                continue
            
            # 5. 导出
            print("   📤 导出中...")
            filepath = await self.export_once()
            
            if filepath:
                downloaded_files.append(filepath)
                print(f"   ✅ 下载成功: {os.path.basename(filepath)}")
                # 记录这批已导出的视频
                for vid in selected_ids:
                    self.exported_videos.add(vid)
                print(f"   📊 累计已导出 {len(self.exported_videos)} 个视频")
            else:
                print(f"   ❌ 导出失败")
            
            await asyncio.sleep(1)
        
        print(f"\n{'=' * 55}")
        print(f"   📊 完成！共导出 {len(downloaded_files)} 个文件")
        print(f"   📊 覆盖 {len(self.exported_videos)} 个视频")
        print(f"{'=' * 55}")
        
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
