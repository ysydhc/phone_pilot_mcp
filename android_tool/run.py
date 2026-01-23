import sys
import os

import asyncio
project_root = os.path.dirname(os.path.abspath('android_tool'))
print(f"project_root:{project_root}")
sys.path.insert(0, project_root)

from android_tool import android_device_utils
from android_tool import device_lock
import mcp_server as mcp
import vision as vision
import screenshot as screenshots
from android_tool.multi_finder import MultiStageFinder
# import android_device_utils as utils


async def main():
    # d = await mcp.android_device_get(device_serial='98e516bf0922', out_dir='./recordings', force_update=True, include_system=True)
    # d = await mcp.android_wake_and_unlock(device_serial='98e516bf0922', pin='1111')
    # d = await device_lock.detect_lock_method(device_serial='98e516bf0922')
    # d = device_lock.wake_and_unlock(device_serial='98e516bf0922', pin='1111')
    # d = await mcp.android_workflow_docs_list()
    # d = await mcp.android_run_workflow_from_doc(doc_path='./recordings/workflow_docs/Gravity_清进程_回桌面_找图进入个人中心_找图进入任务中心_全程录屏_a47662c85405.md')
    # d = await mcp.android_find_image_and_tap(device_serial='98e516bf0922', template_path='/Users/yeshouyou/Work/agent/phone_touch/recordings/templates/gravity_个人中心_503f458d8ac7.png')
    # d = await mcp.android_find_image_candidates(device_serial='98e516bf0922', template_path='/Users/yeshouyou/Work/agent/phone_touch/recordings/templates/gravity_个人中心_503f458d8ac7.png')
    # d = vision.find_icon_with_features(icon_path='/Users/yeshouyou/Work/agent/phone_touch/recordings/templates/gravity_个人中心_503f458d8ac7.png', screenshot_path='./recordings/screen/gravity_screen.png')
    # d = vision.find_template_on_screen_with_fallback(device_serial='98e516bf0922', template_path='/Users/yeshouyou/Work/agent/phone_touch/recordings/templates/gravity_个人中心_503f458d8ac7.png')
    # d = screenshots.save_screenshot_png(device_serial='98e516bf0922', out_path='./recordings/screen/gravity_screen_2.png')

    # msf = MultiStageFinder(device_serial='98e516bf0922')
    # ad_item = msf.find_image(template_path='/Users/yeshouyou/Work/agent/phone_touch/recordings/templates/广告item.png')
    # roi = msf.box_to_roi(ad_item['matches'][0])
    # text = msf.find_text(query='去完成', roi=roi)
    # d = text

    # d = await mcp.android_run_workflow_from_doc(doc_path='./recordings/workflow_docs/ad_go.md')
    # d = await mcp.android_run_workflow_from_doc(doc_path='./recordings/workflow_docs/Gravity_清进程_回桌面_找图进入个人中心_找图进入任务中心_全程录屏_任务中心找广告去完成.md')
    # d = await mcp.android_run_workflow_from_doc(doc_path='./recordings/workflow_docs/gravity_test_memory.md')
    # d = await mcp.android_run_workflow_from_doc(doc_path='/Users/yeshouyou/recordings/workflow_docs/AdMasterDemo_激励视频_加载并展示_20e02ce7c575.md')
    # d = await mcp.android_run_workflow_from_doc(doc_path='./workflow/douyin_swipe_100.json')
    # d = await mcp.android_run_workflow_from_doc(doc_path='/Users/yeshouyou/recordings/workflow_docs/AdMasterDemo_激励视频_加载并展示_20e02ce7c575.md', device_serial='98e516bf0922')
    # d = await mcp.android_run_workflow_from_doc(doc_path='./workflow/settings_gif_memory_test.json')
    d = await mcp.android_run_script(file_path='./workflow/settings_gif_memory_test.script')
    return d

if __name__ == "__main__":
#    d = utils.get_app_label_via_pyaxmlparser(device_serial='ZY22J7FWVJ', package_name='cc.admaster.android.demo', cache_dir='./cache')
#    print(d)
    d = asyncio.run(main())
    print(d)