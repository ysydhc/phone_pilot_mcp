# 三平台功能对照表 (Android / Harmony / iOS)

> 本文档追踪 phone_pilot 在三个平台上的功能实现状态。  
> 每实现一个功能后必须更新本文档。  
> 最后更新: 2026-03-18

## 状态说明

| 标记 | 含义 |
|------|------|
| 已实现 | 功能完整可用 |
| 已实现(HDC) | 仅通过 HDC 原始命令实现（基础能力） |
| 已实现(hmdriver2) | 通过 hmdriver2 库实现（完整能力） |
| 部分实现 | 核心功能可用但有限制 |
| 未实现(有方案) | 尚未实现但有明确技术路线 |
| 未实现 | 尚未实现且无明确方案 |
| 不适用 | 该功能在此平台无意义 |

---

## 1. 输入操作 (Input)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| tap (坐标点击) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | uitest uiInput click |
| swipe (滑动) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | Driver.swipe |
| long_press (长按) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | Driver.long_click |
| double_click (双击) | 未实现 | 已实现(hmdriver2) | 未实现(有方案) | Driver.double_click |
| input_text (文本输入) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 支持完整输入 |
| input_keyevent (按键事件) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | press_key + KeyCode 映射 |
| set_clipboard_text (设置剪贴板) | 已实现 | 未实现(有方案) | 未实现(有方案) | hmdriver2 暂无此 API; 可用 HDC shell |
| complex_gesture (复杂手势) | 未实现 | 未实现(有方案) | 未实现 | hmdriver2 提供 gesture 链式 API |

## 2. 屏幕操作 (Screen)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| screenshot (截图) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screenCap (PNG) + snapshot_display (JPEG快速) |
| save_screenshot_png (截图保存) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 基于 screenshot 封装 |
| png_bytes_to_base64 | 已实现 | 已实现 | 未实现(有方案) | 纯 Python 工具函数 |
| get_screen_size (屏幕尺寸) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | Driver.getDisplaySize |
| get_screen_density (屏幕密度) | 已实现 | 未实现(有方案) | 未实现(有方案) | hmdriver2 device_info 可获取 |
| start_screenrecord (开始录屏) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screenrecord.start |
| stop_screenrecord (停止录屏) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screenrecord.stop |
| display_rotation (屏幕旋转) | 未实现 | 未实现(有方案) | 未实现(有方案) | hmdriver2 提供 display_rotation |
| set_display_rotation | 未实现 | 未实现(有方案) | 未实现(有方案) | hmdriver2 提供 set_display_rotation |

## 3. UI 树操作 (UI Hierarchy)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| dump_ui_hierarchy (导出 UI 树) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | uitest dumpLayout |
| parse_hierarchy_nodes (解析节点) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 递归解析 JSON dict |
| find_nodes (节点查找) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | text/id/type/description/any 选择器 |
| ui_signature (UI 签名) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 基于 UI 树节点计算 |
| pick_scrollable_bounds (滚动容器) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 从 UI 树提取 scrollable 节点 |
| infer_scroll_directions (推断滚动方向) | 已实现 | 未实现(有方案) | 未实现(有方案) | 待实现 |
| UINode 统一数据类 | 已实现 | 已实现 | 未实现(有方案) | core/ui_node.py, 两平台共用 |
| dump_ui_nodes (统一节点列表) | 已实现 | 已实现 | 未实现(有方案) | UIDriver protocol, 返回 list[UINode] |
| collect_all_texts (收集 UI 文本) | 已实现 | 已实现 | 未实现(有方案) | UIDriver protocol |
| dump_memory_profile (内存快照) | 已实现 | 不适用 | 未实现(有方案) | DeviceDriver protocol, Android hprof |

## 4. UI 查找与交互 (UI Finder)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| ~~find_and_tap (复合查找并点击)~~ | ~~已删除~~ | ~~已删除~~ | — | Protocol/Driver/Skills/MCP 均已移除; 推荐 find_elements + tap 组合 |
| ~~find_and_tap_impl (630行复合函数)~~ | ~~已删除~~ | — | — | 三级回退(UI/OCR/图片)巨型函数，已拆解为独立 find + tap |
| ~~dismiss_popups (关闭弹窗)~~ | ~~已删除~~ | ~~已删除~~ | — | 无生产代码引用，已清理 |
| ~~find_on_screen (屏幕查找)~~ | ~~已删除~~ | ~~已删除~~ | — | 已由 UIDriver.find_elements 替代 |
| launch_from_home (从主屏启动) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | DeviceDriver.launch_from_home protocol |
| ~~pick_tab_node (查找 Tab)~~ | ~~已删除~~ | — | — | 无生产代码引用 |
| Clicker (点击辅助类) | 已实现(内部) | 已实现(hmdriver2) | 未实现(有方案) | Android 内部使用 |

## 5. 元素查询 (Element Query)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| element_query (元素查询) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | d(text/id/type).find_component() |
| element_exists (元素存在检查) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | d(...).exists() |
| element_query_async | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | async 封装 |
| element_exists_async | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | async 封装 |
| collect_node_texts (收集文本) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 从 UI 树提取 |
| xpath 选择器 | 未实现 | 未实现(有方案) | 未实现(有方案) | hmdriver2 提供 xpath API |

## 6. 多阶段查找 (Multi-Stage Finder)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| ~~MultiStageFinder (多阶段查找类)~~ | ~~已删除~~ | ~~已删除~~ | — | 无生产代码引用，整个模块已清理 |
| ~~find_multi_stage (执行多阶段查找)~~ | ~~已删除~~ | ~~已删除~~ | — | 同上 |

## 7. 应用管理 (App Management)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| list_packages (列出应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | list_apps |
| launch_app (启动应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | start_app |
| force_stop (强制停止) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | stop_app |
| restart_app (重启应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | stop + start |
| clear_data (清除数据) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | clear_app |
| install_app (安装应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | install_app |
| uninstall_app (卸载应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | uninstall_app |
| get_app_info (应用信息) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | get_app_info |
| get_installed_apps (带元数据) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | list_apps + app_info |
| has_app (应用是否安装) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | has_app |
| get_app_main_ability | 不适用 | 已实现(hmdriver2) | 不适用 | get_app_main_ability |
| open_deeplink (打开深链接) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | open_url |
| get_current_focus (前台应用) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | current_app |
| is_launcher_package | 已实现 | 未实现(有方案) | 未实现(有方案) | 需 app_info 判断 |
| get_default_launcher_component | 已实现 | 未实现(有方案) | 不适用 | 需 ability 列表判断 |
| list_launcher_components | 已实现 | 未实现(有方案) | 不适用 | 同上 |
| build_launch_profiles_snapshot | 已实现 | 未实现(有方案) | 未实现(有方案) | 需多项数据聚合 |
| clear_background_processes | 已实现 | 未实现(有方案) | 未实现(有方案) | Harmony: aa force-stop 逐个停止 |

## 8. 设备管理 (Device Management)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| get_device_profile (设备画像) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | device_info |
| get_android_os_info (系统信息) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | device_info 含 sdkVersion |
| capture_device_profile (采集并存储) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 复用 core/storage |
| get_device_profile_from_store | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 复用 core/storage |
| list_devices_from_store | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 复用 core/storage |
| set_device_unlock_pin | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| get_device_unlock_pin | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| set_device_unlock_method | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| get_device_unlock_method | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| get_device_screenrecord_prefs | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| record_screenrecord_success | 已实现 | 已实现 | 未实现(有方案) | 复用 core/storage |
| get_device_locales (设备语言) | 已实现 | 未实现(有方案) | 未实现(有方案) | HDC shell 获取 |
| get_touch_abs_max | 已实现 | 不适用 | 不适用 | Harmony 触摸坐标机制不同 |

## 9. 锁屏/解锁 (Lock/Unlock)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| wake_and_unlock (唤醒解锁) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | unlock() |
| lock_screen (锁屏) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screen_off() |
| detect_lock_method | 已实现 | 未实现(有方案) | 未实现(有方案) | 需 dumpsys 解析 |
| get_device_state (设备状态) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | get_screen_state |
| get_display_state (屏幕状态) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screen_on/screen_off 状态 |
| screen_on (亮屏) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screen_on() |
| screen_off (息屏) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | screen_off() |

## 10. 日志 (Logcat / HiLog)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| clear_logcat (清除日志) | 已实现 | 已实现(HDC) | 未实现(有方案) | hdc shell hilog -r |
| read_logcat (读取日志) | 已实现 | 已实现(HDC) | 未实现(有方案) | hdc shell hilog |
| dump_logcat (导出日志) | 已实现 | 已实现(HDC) | 未实现(有方案) | hilog 导出到文件 |

## 11. 断言 (Assertions)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| assert_element_exists | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | UI 树 + element_exists |
| assert_element_not_exists | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 同上 |
| assert_text_equals | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | UI 树 + OCR |
| assert_text_contains | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 同上 |
| assert_activity | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | current_app 检查 |
| assert_logcat | 已实现 | 已实现(HDC) | 未实现(有方案) | hilog 读取 |
| assert_impl (统一断言入口) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 上层封装 |
| assert_async | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | async 封装 |

## 12. 等待 (Wait)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| wait_for_element_appear | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | UI 树轮询 |
| wait_for_element_disappear | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 同上 |
| wait_for_activity | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | current_app 检查 |
| wait_for_page_stable | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | UI 签名比较 |
| wait_for_impl (统一等待入口) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | 上层封装 |
| wait_for_async | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | async 封装 |

## 13. 录制回放 (Recording & Playback)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| ~~android_record_taps_with_ui~~ | ~~已删除~~ | — | — | 无生产代码引用，已清理 |
| android_record_start | 已实现 | 未实现 | 未实现 | getevent 录制 |
| android_record_stop | 已实现 | 未实现 | 未实现 | getevent 停止 |
| start_getevent_recording | 已实现 | 不适用 | 不适用 | Android getevent 专有 |
| start_getevent_recording_detached | 已实现 | 不适用 | 不适用 | 同上 |
| stop_getevent_recording | 已实现 | 不适用 | 不适用 | 同上 |
| parse_getevent | 已实现 | 不适用 | 不适用 | 同上 |
| to_monkey_script | 已实现 | 不适用 | 不适用 | Monkey 专有 |
| convert_raw_to_mks | 已实现 | 不适用 | 不适用 | Monkey 专有 |
| scale_mks_for_screen | 已实现 | 不适用 | 不适用 | Monkey 专有 |
| android_push_and_run_monkey | 已实现 | 不适用 | 不适用 | Monkey 专有 |
| MonkeyRunner | 已实现 | 不适用 | 不适用 | Monkey 专有 |

## 14. 采集 (Collect)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| collect_artifacts (截图+日志+焦点) | 已实现 | 已实现(hmdriver2+HDC) | 未实现(有方案) | 截图 + hilog + current_app |

## 15. HDC/ADB 工具 (Platform Tools)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| CommandRunner (命令执行) | 已实现 | 已实现(HDC) | 未实现(有方案) | HdcCommandRunner |
| push_file (推送文件) | 已实现 | 已实现(HDC) | 未实现(有方案) | hdc file send |
| pull_file (拉取文件) | 已实现 | 已实现(HDC) | 未实现(有方案) | hdc file recv |
| wait_for_device | 已实现 | 已实现(HDC) | 未实现(有方案) | hdc wait |
| hdc/adb_executable (路径解析) | 已实现 | 已实现(HDC) | 不适用 | 含 DevEco-Studio 路径 |

## 16. Toast 监听

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| toast_watcher (Toast 监听) | 未实现 | 未实现(有方案) | 未实现 | hmdriver2 提供 toast_watcher |

## 17. 截图+元素标注 (Screenshot + Element Annotation) — 新增

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| phone_get_page_state(annotate_elements) | 已实现 | 已实现 | 未实现(有方案) | 截图叠加元素编号+边框标注 |
| phone_tap_element(index) | 已实现 | 已实现 | 未实现(有方案) | 通过元素编号点击（配合 page_state） |
| annotate_elements_on_screenshot | 已实现 | 已实现 | 已实现 | 纯 Python+CV2, 平台无关 |
| screenshot_annotated (script_api) | 已实现 | 已实现 | 未实现(有方案) | 截图+标注合一保存到 RunSession |

## 18. 验证管道 (Verification Pipeline)

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| phone_run_script (脚本执行) | 已实现 | 已实现 | 未实现(有方案) | 执行 Python 工作流脚本 |
| phone_verify (批量断言) | 已实现 | 已实现 | 未实现(有方案) | UI/日志/内存/性能/截图 |
| phone_checkpoint_save (检查点保存) | 已实现 | 已实现 | 未实现(有方案) | 截图+UI文本+Activity |
| phone_checkpoint_diff (检查点对比) | 已实现 | 已实现 | 未实现(有方案) | 文本diff+截图相似度 |
| UIGraph (DAG 拓扑引擎) | 已实现 | 已实现 | 已实现 | 纯 Python, 平台无关 |
| chain assert_text_exists | 已实现 | 已实现 | 未实现(有方案) | 支持 re: 正则前缀 |
| chain assert_logcat_contains | 已实现 | 已实现 | 未实现(有方案) | 支持 re: 正则前缀 |
| chain checkpoint | 已实现 | 已实现 | 未实现(有方案) | 在 chain 中保存检查点 |
| 代码变更记录 (git diff) | 已实现 | 已实现 | 已实现 | 纯 Python, 平台无关 |

## 19. 错误自愈 (Error Self-Healing) — 新增

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| PopupGuard (弹窗自动检测与关闭) | 已实现 | 部分实现 | 未实现(有方案) | Window层级(Android)+UI树+文本规则 |
| HealingExperienceStore (纠正经验库) | 已实现 | 已实现 | 已实现 | 纯 Python, 平台无关 |
| SelfHealer (自愈编排器) | 已实现 | 已实现 | 已实现 | 纯 Python, 平台无关 |
| LLM 全局分析 (可选兜底) | 已实现 | 已实现 | 已实现 | 需 OPENAI_API_KEY |
| find_text 智能重试 | 已实现 | 已实现 | 未实现(有方案) | 重试间隙调用 SelfHealer |
| find_image 智能重试 | 已实现 | 已实现 | 未实现(有方案) | 重试间隙调用 SelfHealer |
| retry() ctx 参数 | 已实现 | 已实现 | 已实现 | 通用重试 + 自愈 |
| 自愈事件持久化 | 已实现 | 已实现 | 已实现 | healing_events.json |
| 跨脚本经验复用 | 已实现 | 已实现 | 已实现 | 按 action+query+app+activity 匹配 |

## 20. HTML 可视化报告 (HTML Visual Report) — 新增

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| HTML 报告自动生成 | 已实现 | 已实现 | 已实现 | 纯 Python + Jinja2, 平台无关 |
| 步骤时间线 | 已实现 | 已实现 | 已实现 | 截图缩略图 + lightbox |
| 截图画廊 | 已实现 | 已实现 | 已实现 | 网格布局, 点击放大 |
| 日志摘要 | 已实现 | 已实现 | 已实现 | logcat 文件列表 + console.log 预览 |
| 内存分析区 | 已实现 | 已实现 | 已实现 | before/after/diff 表格 |
| 自愈事件区 | 已实现 | 已实现 | 已实现 | 修复类型 + 成功率统计 |
| auto_report 开关 | 已实现 | 已实现 | 已实现 | ScriptContext.auto_report |
| 深/浅色自适应 | 已实现 | 已实现 | 已实现 | @media prefers-color-scheme |

## 21. MCP 工具层 (MCP Tools) — 新增

> 以下为 MCP server 暴露的 `phone_*` 工具，Agent 通过 MCP 协议直接调用。

| 功能点 | Android | Harmony | iOS | 备注 |
|--------|---------|---------|-----|------|
| phone_go_home (返回桌面) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.go_home |
| phone_go_back (返回上一页) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.go_back |
| phone_unlock (解锁) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.unlock |
| phone_clear_background (清后台) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.clear_background |
| phone_clear_data (清数据) | 已实现 | 已实现 | 未实现(有方案) | AppDriver.clear_data |
| phone_open_deeplink (深链接) | 已实现 | 已实现(hmdriver2) | 未实现(有方案) | DeviceDriver.open_deeplink |
| phone_start_recording (开始录屏) | 已实现 | 已实现 | 未实现(有方案) | MCP 状态管理 |
| phone_stop_recording (停止录屏) | 已实现 | 已实现 | 未实现(有方案) | 自动 pull + remove |
| phone_start_logcat (开始日志) | 已实现 | 已实现 | 未实现(有方案) | MCP 状态管理 |
| phone_stop_logcat (停止日志) | 已实现 | 已实现 | 未实现(有方案) | 自动停止采集 |
| phone_search_logcat (搜索日志) | 已实现 | 已实现 | 未实现(有方案) | 支持正则 |
| phone_scroll_to_find (滚动查找) | 已实现 | 已实现 | 未实现(有方案) | 复用 script_api |
| phone_wait_for_element (等待元素) | 已实现 | 已实现 | 未实现(有方案) | 轮询 UI 树 |
| phone_dismiss_popup (关闭弹窗) | 已实现 | 已实现 | 未实现(有方案) | PopupGuard |
| phone_smart_find (智能查找) | 已实现 | 已实现 | 未实现(有方案) | UIA+OCR+弹窗 |
| phone_launch_from_home (桌面启动) | 已实现 | 已实现 | 未实现(有方案) | script_api |
| phone_install_app (安装) | 已实现 | 已实现 | 未实现(有方案) | APK/HAP |
| phone_uninstall_app (卸载) | 已实现 | 已实现 | 未实现(有方案) | AppDriver.uninstall |
| phone_memory_snapshot (内存快照) | 已实现 | 不适用 | 未实现(有方案) | Android meminfo |
| phone_memory_check_leak (泄漏检测) | 已实现 | 不适用 | 未实现(有方案) | Activity 泄漏 |
| phone_pull_file (拉取文件) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.pull_file |
| phone_push_file (推送文件) | 已实现 | 已实现 | 未实现(有方案) | DeviceDriver.push_file |
| phone_read_clipboard (读剪贴板) | 已实现 | 未实现(有方案) | 未实现(有方案) | Android 10+ |
| phone_get_notifications (通知) | 已实现 | 未实现(有方案) | 未实现(有方案) | dumpsys notification |
| phone_toggle_wifi (WiFi) | 已实现 | 未实现(有方案) | 未实现(有方案) | svc wifi |
| phone_toggle_airplane (飞行模式) | 已实现 | 未实现(有方案) | 未实现(有方案) | settings + broadcast |
| phone_execute_shell (受限 shell) | 已实现 | 未实现(有方案) | 未实现(有方案) | 白名单限制 |
| phone_get_device_info (设备信息) | 已实现 | 已实现 | 未实现(有方案) | 轻量级信息 |

---

## iOS 技术路线 (规划中)

iOS 目前仅有 stub 文件，所有方法抛出 `NotImplementedError`。技术路线：

- **输入/截图**: libimobiledevice + WebDriverAgent
- **UI 树**: WebDriverAgent XCTest
- **应用管理**: ideviceinstaller / pymobiledevice3
- **日志**: idevicesyslog
- iOS 实现不在本次迭代范围内，仅在文档中规划

## 统计

| 平台 | 已实现 | 部分实现 | 未实现(有方案) | 未实现 | 不适用 |
|------|--------|----------|----------------|--------|--------|
| Android | ~120 | 0 | 0 | ~5 | 0 |
| Harmony | ~90 | 0 | ~20 | ~5 | ~8 |
| iOS | 0 | 0 | ~80 | ~5 | ~8 |
