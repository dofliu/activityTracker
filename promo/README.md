# OmniContext 介紹影片(3 分鐘)場景源檔

18 個 standalone HTML 動畫場景 + 分鏡表,由 `repo-intro-video` 技能產生;
成片為 1080p30 MP4(2:49.8,分鏡 180 秒扣除 17 個 0.6 秒 xfade 轉場重疊)。

## 重新產生 / 微調

```bash
pip install playwright imageio-ffmpeg   # 瀏覽器用系統 Chromium(CHROMIUM_PATH 指向執行檔)
# 1. 改某一景的文案 → 只重渲那一景
python render_scenes_local.py storyboard.json --workdir work --only scene06_summary
# 2. 全部渲染
python render_scenes_local.py storyboard.json --workdir work
# 3. 串接出片(--bed 安靜氛圍音;--music your.mp3 換自己的音樂;不加參數則無聲)
python <repo-intro-video skill>/scripts/assemble_video.py storyboard.json --workdir work --out intro.mp4 --bed
```

注意:`render_scenes_local.py` 需要「完整版」ffmpeg 在 PATH(含 PNG 解碼與 libx264;
`imageio-ffmpeg` 內附的靜態版即可)——Playwright 內建的精簡 ffmpeg 沒有 PNG 解碼器。
每景渲完會產出 `check_<scene>.png` 抽查格,改版後請先看排版再出片。
