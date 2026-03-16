# MCP 工具验证检查表

工具列表与 A/B/C 分类由 `scripts/generate_mcp_tool_list.py` 生成，见 `docs/verification/mcp_tool_list_generated.md`，请勿手改。本文件仅记录每工具执行结果（通过/失败/跳过）与备注。

---


| 工具名                            | 类型  | 通过标准摘要                                                                     | 执行结果 | 备注                                                         |
| ------------------------------ | --- | -------------------------------------------------------------------------- | ---- | ---------------------------------------------------------- |
| phone_list_devices | A | ok, devices | 通过 |  |
| **phone_build_device_profile** | A   | ok                                                                         | 失败   | parse_failed_or_empty                                      |
| phone_export_device_info_md | A | ok, md_path | 通过 |  |
| phone_screenshot | B | ok | 通过 |  |
| phone_get_screen_size | A | ok, width, height | 通过 |  |
| phone_start_recording | B | ok, local_path | 通过 |  |
| phone_stop_recording | B | ok, local_path | 通过 |  |
| phone_go_home | B | ok, platform | 通过 |  |
| phone_go_back | B | ok, platform | 通过 |  |
| phone_unlock | B | ok, platform | 通过 |  |
| phone_clear_background | B | ok, platform | 通过 |  |
| phone_clear_data | A | ok, package | 失败 | error |
| phone_open_deeplink | B | ok, uri | 通过 |  |
| phone_tap | B | ok, x, y, platform | 通过 |  |
| phone_swipe | B | ok, x1, y1, x2, y2, platform | 通过 |  |
| phone_long_press | B | ok, x, y, platform | 通过 |  |
| phone_input_text | B | ok, text, platform | 通过 |  |
| phone_keyevent | B | ok, key, platform | 通过 |  |
| phone_find_element | B | ok, count, elements | 通过 |  |
| phone_get_current_activity | A | ok, package, activity | 通过 |  |
| phone_find_image | B | ok, count, matches | 通过 |  |
| phone_ocr_find | B | ok | 通过 |  |
| phone_launch_app | B | ok, package | 通过 |  |
| phone_force_stop | B | ok, package | 通过 |  |
| phone_list_packages | A | ok, count, packages | 通过 |  |
| phone_compare_screenshot | B | ok, match, similarity, threshold | 通过 |  |
| phone_res_add | A | ok, record | 通过 |  |
| phone_res_update | A | ok, record | 通过 |  |
| phone_res_delete | A | ok, deleted | 通过 |  |
| phone_res_get | A | ok, record | 通过 |  |
| phone_res_list | A | ok, records, count | 通过 |  |
| phone_res_resolve | A | ok, value, resolved, cached | 通过 |  |
| phone_recordings_list | A | ok, recordings, count | 通过 |  |
| phone_recordings_get           | C   | ok                                                                         | 跳过   | 依赖 recording_key 与已有录制文件；需先有录制再验                           |
| phone_replay_recording | B | ok | 失败 | tool_response_not_json |
| phone_push_and_run_monkey | B | ok | 失败 | tool_response_not_json |
| phone_run_script               | C   | ok, result, stdout, elapsed_ms                                             | 跳过   | 依赖本地脚本与 main(ctx)；可用最小脚本（go_home+get_current_activity）项目内验 |
| phone_verify                   | C   | ok, passed, failed, total, results                                         | 跳过   | 依赖 assertions JSON 与界面/日志/内存；可选 1～2 种断言类型在设置首页验            |
| phone_checkpoint_save | B | ok, name, path | 通过 |  |
| phone_checkpoint_diff | B | ok | 通过 |  |
| phone_start_logcat | A | ok, path | 通过 |  |
| phone_stop_logcat | A | ok | 通过 |  |
| phone_search_logcat | A | ok, matches, count | 通过 |  |
| phone_scroll_to_find | B | ok, center, bounds, text | 通过 |  |
| phone_wait_for_element | B | ok, found | 通过 |  |
| phone_dismiss_popup | B | ok, dismissed | 通过 |  |
| phone_smart_find | B | ok, center, bounds, text | 通过 |  |
| phone_launch_from_home | B | ok | 通过 |  |
| phone_install_app | A | ok, apk_path | 通过 |  |
| phone_uninstall_app | A | ok, package | 失败 | error |
| phone_memory_snapshot | A | ok, summary | 通过 |  |
| phone_memory_check_leak        | C   | ok, leaked                                                                 | 跳过   | 依赖已安装包与 hprof/分析环境；需准备被测包与环境                               |
| phone_pull_file | A | ok, local_path, remote_path | 通过 |  |
| phone_push_file | A | ok, local_path, remote_path | 通过 |  |
| phone_read_clipboard | A | ok, text | 通过 |  |
| phone_get_notifications | A | ok, notifications | 通过 |  |
| phone_toggle_wifi | A | ok, wifi_on | 通过 |  |
| phone_toggle_airplane | A | ok, airplane_on | 通过 |  |
| phone_execute_shell | C | ok, stdout, stderr, returncode | 跳过 | 不验证（C 类） |
| phone_get_device_info | B | ok, screen, activity | 通过 |  |
| phone_get_page_state           | C   | ok, activity, screen, elements, element_count, scrollable_areas, all_texts | 跳过   | 返回结构复杂、与界面强相关；可在设置首页验 ok/activity/elements/all_texts 或人工核对 |
| phone_tap_element | B | ok, index, center, label, type | 通过 |  |


