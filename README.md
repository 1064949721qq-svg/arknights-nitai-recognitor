# 萨卡兹商店六星藏品识别原型

这个原型用于识别萨卡兹肉鸽商店中是否正在出售 `去伪存真藏品表 哲版` 里的六星藏品。数据来自 `/Users/zhugecunfu/Desktop/文档/sakazi.xlsx`，共 46 个六星藏品。

## 文件说明

- `sakazi_shop_detector.py`：Python 控制端，支持 Tkinter 按钮界面和命令行单次识别。
- `resource/pipeline/shop_detect.json`：MaaFramework Pipeline。入口为 `StartShopDetect`，按顺序 OCR 商店区域里的 46 个藏品名。
- `resource/default_pipeline.json`：OCR 和默认超时参数。
- `data/six_star_collectibles.json`：藏品名单。
- `config.example.json`：ADB、ROI、OCR 阈值等配置。

## 使用

```bash
cd /Users/zhugecunfu/Documents/Codex/2026-06-05/files-mentioned-by-the-user-sakazi/outputs/sakazi_shop_detector
python -m pip install -r requirements.txt
python sakazi_shop_detector.py
```

进入游戏商店界面后，点击窗口里的“开始识别”。

命令行单次识别：

```bash
python sakazi_shop_detector.py --once
```

只测试藏品名匹配逻辑：

```bash
python sakazi_shop_detector.py --mock-text "商店里有精神治疗录像带和四叶草化石"
```

## 需要调的参数

`config.example.json` 里的 `shop_roi_720p` 是 MaaFramework 的 720p 坐标系 ROI，默认 `[210, 110, 900, 470]`。如果你的模拟器窗口、商店布局或缩放不同，优先调整这个区域。

ADB 示例地址 `127.0.0.1:16384` 需要改成你的模拟器端口。不同模拟器常见端口不同，MaaFramework 也可以通过工具枚举 ADB 设备后再填入。

## 实现依据

MaaFramework Pipeline 通过入口节点的 `next` 列表顺序识别后续节点；命中某个节点后会停止后续检测并执行动作。这里每个六星藏品是一个 OCR 检测节点，动作为 `DoNothing`，所以不会点击或购买，只返回是否命中。
