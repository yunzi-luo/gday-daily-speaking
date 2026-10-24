# G'day 每日口语

每天一课澳洲生活场景英语口语练习：打电话、同事闲聊、看病、租房、孩子学校……

每课包括核心词汇、听力难点（连读/吞音）、两分钟演讲范文、情景对话和救命句，所有英文都可以点开朗读。

网站：https://yunzi-luo.github.io/gday-daily-speaking/

## 结构

- `index.html`：网站页面
- `lessons/index.json`：课程目录（新课在最前）
- `lessons/YYYY-MM-DD.json`：每天一课的内容（`dialogue.other.voice` 填 `m`/`f`，决定对方用男声还是女声）
- `lessons/audio/`：每课的 AI 配音。新课推送到 main 后由 GitHub Actions（`.github/workflows/voice-clips.yml`）自动生成；也可以本地运行 `python tools/make_audio.py YYYY-MM-DD`。没有配音的课自动用浏览器朗读

新课每天早上自动加入。

## 版权

课程内容和网站代码版权所有，个人学习可免费使用，转载或商用需要授权。详见 [LICENSE.md](LICENSE.md)。
