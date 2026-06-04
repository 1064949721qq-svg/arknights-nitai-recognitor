from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import traceback
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


@dataclass
class DetectionResult:
    hit: bool
    matches: list[str]
    raw: str


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def find_collectibles_in_text(text: str, names: list[str]) -> list[str]:
    normalized = normalize_text(text)
    hits = []
    for name in names:
        if normalize_text(name) in normalized:
            hits.append(name)
    return hits


def to_plain_data(value: Any, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    value_id = id(value)
    if value_id in seen:
        return repr(value)
    seen.add(value_id)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple, set)):
        return [to_plain_data(item, seen) for item in value]
    if isinstance(value, dict):
        return {str(k): to_plain_data(v, seen) for k, v in value.items()}
    if is_dataclass(value):
        return {
            field.name: to_plain_data(getattr(value, field.name), seen)
            for field in fields(value)
            if field.name != "raw_image"
        }
    if hasattr(value, "task_id") and hasattr(value, "entry") and hasattr(value, "nodes"):
        return {
            "task_id": to_plain_data(getattr(value, "task_id"), seen),
            "entry": to_plain_data(getattr(value, "entry"), seen),
            "status": repr(getattr(value, "status", "")),
            "nodes": to_plain_data(getattr(value, "nodes"), seen),
        }
    return repr(value)


def dump_any(value: Any) -> str:
    plain = to_plain_data(value)
    try:
        return json.dumps(plain, ensure_ascii=False, indent=2)
    except TypeError:
        return repr(value)


def build_pipeline_override(config: dict[str, Any]) -> dict[str, Any]:
    roi = config.get("shop_roi_720p", [210, 110, 900, 470])
    threshold = config.get("ocr_threshold", 0.3)
    timeout = config.get("timeout_ms", 2500)
    pipeline = load_json(ROOT / "resource" / "pipeline" / "shop_detect.json")
    override: dict[str, Any] = {
        "StartShopDetect": {
            "next": pipeline["StartShopDetect"]["next"],
            "timeout": timeout,
        },
    }
    for node_name, node in pipeline.items():
        if not node_name.startswith("Detect_"):
            continue
        override[node_name] = {
            "roi": roi,
            "threshold": threshold,
            "expected": node["expected"],
        }
    return override


def enum_value(enum_cls: Any, value: Any) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError(f"不支持的枚举值：{value!r}")

    result = 0
    for part in re.split(r"[|,+\s]+", value):
        if not part:
            continue
        try:
            result |= int(getattr(enum_cls, part))
        except AttributeError as exc:
            names = [name for name in dir(enum_cls) if not name.startswith("_")]
            raise ValueError(f"未知 MaaFramework 枚举值 {value!r}，可用值：{', '.join(names)}") from exc
    return result


def contains_any(text: str, keywords: list[str]) -> bool:
    normalized = text.casefold()
    return any(keyword.casefold() in normalized for keyword in keywords)


class MaaShopDetector:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = load_json(config_path)
        collectibles_path = self._resolve(self.config["collectibles_file"])
        self.collectibles = load_json(collectibles_path)

    def _resolve(self, configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return ROOT / path

    def run_mock(self, text: str) -> DetectionResult:
        matches = find_collectibles_in_text(text, self.collectibles)
        return DetectionResult(bool(matches), matches, text)

    def run_once(self) -> DetectionResult:
        task_result = self._run_maa_task()
        raw = dump_any(task_result)
        matches = find_collectibles_in_text(raw, self.collectibles)
        completed = bool(getattr(task_result, "completed", False))
        return DetectionResult(bool(matches) or completed, matches, raw)

    def _run_maa_task(self) -> Any:
        try:
            from maa.resource import Resource
            from maa.tasker import Tasker
        except ImportError as exc:
            raise RuntimeError(
                "未找到 MaaFramework Python 绑定。请先运行：python -m pip install maafw"
            ) from exc

        resource = Resource()
        resource_dir = self._resolve(self.config["resource_dir"])
        resource.post_bundle(str(resource_dir)).wait()

        controller = self._create_controller()
        controller.post_connection().wait()
        display_short_side = int(self.config.get("display_short_side", 720))
        if display_short_side > 0:
            controller.set_screenshot_target_short_side(display_short_side)

        tasker = Tasker()
        if not tasker.bind(resource, controller):
            raise RuntimeError("MaaFramework Tasker 绑定资源或控制器失败。")
        if not tasker.inited:
            raise RuntimeError("MaaFramework Tasker 初始化失败，请检查 resource/ADB/OCR 模型配置。")

        override = build_pipeline_override(self.config)
        job = tasker.post_task(self.config.get("entry", "StartShopDetect"), override)
        return job.wait().get()

    def _create_controller(self) -> Any:
        mode = self.config.get("controller", {}).get("mode", "auto").lower()
        if mode in {"auto", "pc", "win32"}:
            try:
                return self._create_win32_controller()
            except RuntimeError:
                if mode in {"pc", "win32"}:
                    raise
        return self._create_adb_controller()

    def _create_win32_controller(self) -> Any:
        from maa.controller import Win32Controller
        from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
        from maa.toolkit import Toolkit

        pc = self.config.get("pc", {})
        title_keywords = pc.get("title_keywords", ["明日方舟", "Arknights"])
        class_keywords = pc.get("class_keywords", ["UnityWndClass"])
        exclude_keywords = pc.get("exclude_keywords", ["启动器", "launcher", "鹰角启动器"])

        windows = Toolkit.find_desktop_windows()
        candidates = []
        for window in windows:
            text = f"{window.class_name} {window.window_name}"
            if contains_any(text, exclude_keywords):
                continue
            title_hit = contains_any(window.window_name, title_keywords)
            class_hit = contains_any(window.class_name, class_keywords)
            if title_hit or class_hit:
                score = (2 if title_hit else 0) + (1 if class_hit else 0)
                candidates.append((score, window))

        if not candidates:
            related = [
                f"{window.class_name} / {window.window_name}"
                for window in windows
                if contains_any(f"{window.class_name} {window.window_name}", title_keywords + class_keywords)
            ]
            detail = "\n".join(related[:10]) or "未发现明日方舟相关窗口"
            raise RuntimeError(f"未找到明日方舟 PC 端窗口。请先启动游戏并停在商店界面。\n{detail}")

        window = sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
        screencap_method = enum_value(
            MaaWin32ScreencapMethodEnum,
            pc.get("screencap_method", "Background"),
        )
        mouse_method = enum_value(
            MaaWin32InputMethodEnum,
            pc.get("mouse_method", "Seize"),
        )
        keyboard_method = enum_value(
            MaaWin32InputMethodEnum,
            pc.get("keyboard_method", "Seize"),
        )
        return Win32Controller(window.hwnd, screencap_method, mouse_method, keyboard_method)

    def _create_adb_controller(self) -> Any:
        from maa.controller import AdbController
        from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
        from maa.toolkit import Toolkit

        adb = self.config.get("adb", {})
        configured_adb_path = adb.get("adb_path", "adb")
        adb_path = configured_adb_path
        if configured_adb_path != "adb":
            adb_path = str(self._resolve(configured_adb_path))

        address = adb.get("address", "auto")
        config = adb.get("config", {})
        if isinstance(config, str):
            config = json.loads(config or "{}")

        if address == "auto":
            devices = Toolkit.find_adb_devices(adb_path)
            if not devices:
                raise RuntimeError("未自动发现 ADB 设备；如果使用模拟器，请确认模拟器已启动并开启 ADB。")
            device = devices[0]
            adb_path = str(device.adb_path)
            address = device.address
            screencap_methods = device.screencap_methods
            input_methods = device.input_methods
            config = device.config
        else:
            screencap_methods = enum_value(
                MaaAdbScreencapMethodEnum,
                adb.get("screencap_method", "Default"),
            )
            input_methods = enum_value(
                MaaAdbInputMethodEnum,
                adb.get("input_method", "Default"),
            )

        return AdbController(
            adb_path,
            address,
            screencap_methods,
            input_methods,
            config,
            adb.get("agent_path", ""),
        )


class DetectorGui:
    def __init__(self, detector: MaaShopDetector):
        import tkinter as tk
        from tkinter import scrolledtext

        self.detector = detector
        self.root = tk.Tk()
        self.root.title("萨卡兹商店六星藏品识别")
        self.root.geometry("720x480")

        self.status = tk.StringVar(value="待机：进入商店界面后点击开始识别")
        tk.Label(self.root, textvariable=self.status, anchor="w").pack(fill="x", padx=12, pady=(12, 6))
        tk.Button(self.root, text="开始识别", command=self.start_detection, height=2).pack(fill="x", padx=12)
        self.output = scrolledtext.ScrolledText(self.root, wrap="word", height=22)
        self.output.pack(fill="both", expand=True, padx=12, pady=12)

    def start_detection(self) -> None:
        self.status.set("识别中...")
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self) -> None:
        try:
            result = self.detector.run_once()
            if result.matches:
                message = "命中六星藏品：" + "、".join(result.matches)
            elif result.hit:
                message = "Maa 任务命中，但返回详情里没有解析出具体藏品名；请查看原始结果。"
            else:
                message = "未发现去伪存真六星藏品。"
            self._show(message + "\n\n" + result.raw)
        except Exception:
            self._show(traceback.format_exc())

    def _show(self, text: str) -> None:
        def update() -> None:
            self.status.set("完成")
            self.output.delete("1.0", "end")
            self.output.insert("1.0", text)

        self.root.after(0, update)

    def mainloop(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="识别萨卡兹肉鸽商店是否出售去伪存真六星藏品")
    default_config = ROOT / "config.json"
    if not default_config.exists():
        default_config = ROOT / "config.example.json"
    parser.add_argument("--config", default=str(default_config), help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一次并在命令行输出结果")
    parser.add_argument("--mock-text", default="", help="不连接 MaaFramework，用一段文本测试匹配逻辑")
    args = parser.parse_args(argv)

    detector = MaaShopDetector(Path(args.config))
    if args.mock_text:
        result = detector.run_mock(args.mock_text)
    elif args.once:
        result = detector.run_once()
    else:
        DetectorGui(detector).mainloop()
        return 0

    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.hit else 2


if __name__ == "__main__":
    raise SystemExit(main())
