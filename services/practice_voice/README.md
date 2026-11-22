# 自托管中文答辩语音

这个可选服务在 CPU 上运行两个模型。MeloTTS 朗读系统已经出好的答辩问题，Vosk 将学生的普通话回答转为文字。两个模型独立绑定。模型下载完成后，合成和转写可断网运行，无需厂商 API Key，也没有按字数、时长或调用次数收取的语音接口费。主机、存储和下载流量由部署方承担。负责检索和评分的向量、会话模型沿用各自的供应商与计费方式。

## 安装并启动

下面以 Linux x86_64、Python 3.12 和直接运行在同一台主机上的业务后端为例。从项目根目录执行，语音依赖和模型安装在当前用户的数据目录中。

```bash
cd services/practice_voice
voice_dir="${XDG_DATA_HOME:-$HOME/.local/share}/thesis-practice-voice"
mkdir -p "$voice_dir"
python3.12 -m venv "$voice_dir/venv"
"$voice_dir/venv/bin/python" -m pip install -r requirements.txt
"$voice_dir/venv/bin/python" fetch_models.py "$voice_dir/models"
"$voice_dir/venv/bin/python" server.py --models "$voice_dir/models" --port 18285
```

`fetch_models.py` 下载固定版本的量化 MeloTTS 和 `vosk-model-small-cn-0.22`，校验 SHA-256 后安装。首次下载需要访问 Hugging Face 和 Alpha Cephei。重启服务时复用同一模型目录。长期运行时，可将最后一条命令交给主机的进程管理器，使用虚拟环境、模型目录和 `server.py` 的绝对路径。

服务仅监听 `127.0.0.1:18285`。如果业务后端运行在容器中，这个地址指向后端容器自身。使用容器部署语音服务时，让两个服务共享网络命名空间（例如 Docker Compose 的 `network_mode: "service:backend"`），然后从业务后端所在的网络中验证下方地址。

## 检查模型

在第二个终端检查模型清单。安装完整时，响应中的两个 `id` 分别为 `melo-tts-zh` 和 `vosk-small-cn`。

```bash
curl --fail --silent --show-error http://127.0.0.1:18285/v1/models
```

可先用一条短问题实测合成。下列命令将结果保存到临时目录，并打印采样率和时长。

```bash
voice_check_dir="$(mktemp -d)"
curl --fail --silent --show-error http://127.0.0.1:18285/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"melo-tts-zh","input":"请说明你的系统如何处理重复提交。","voice":"default","response_format":"wav"}' \
  -o "$voice_check_dir/question.wav"
python3.12 - "$voice_check_dir/question.wav" <<'PY'
import sys
import wave

with wave.open(sys.argv[1]) as audio:
    print(f"{audio.getframerate()} Hz, {audio.getnframes() / audio.getframerate():.2f} 秒")
PY
```

如已安装 FFmpeg，可将同一段语音转换为录音接口使用的单声道 16 kHz PCM16，再检查 Vosk 是否返回含中文字的 `text`。这一步可以在没有麦克风的服务器上完成。

```bash
ffmpeg -loglevel error -i "$voice_check_dir/question.wav" -ac 1 -ar 16000 -c:a pcm_s16le "$voice_check_dir/answer.wav"
curl --fail --silent --show-error http://127.0.0.1:18285/v1/audio/transcriptions \
  -F 'model=vosk-small-cn' -F "file=@$voice_check_dir/answer.wav;type=audio/wav"
```

## 绑定数据库供应商

1. 管理员打开「系统设置 → AI 服务 → 添加供应商」，选择「自定义(OpenAI 兼容)」。名称可填「本机中文答辩语音」，Base URL 填 `http://127.0.0.1:18285/v1`，API Key 留空，启用并保存。
2. 在该供应商所在行点「刷新模型」。待清单出现上述两个模型后，在「能力绑定」中为「答辩语音」选择该供应商和 `melo-tts-zh`，为「回答转写」选择同一供应商和 `vosk-small-cn`，点「保存绑定」。
3. 为出题与评分配置「会话模型」，为学生材料索引配置「向量模型」。语音模型仅负责把已有问题读出、把口头回答转成文字。供应商、模型和能力绑定由后端从数据库读取。

## 从学生端验收

1. 通过 HTTPS 或本机地址访问学生端「AI 知识准备」，上传本人报告和源码 ZIP，等待索引就绪。
2. 在「AI 模拟答辩」点击「开始视频答辩」，允许浏览器使用摄像头和麦克风。应能看见实时镜头、听见完整的中文提问。正常现场只显示画面与图标。
3. 题目播完后直接口述，讲完停顿约三秒。系统自动将录音发给配置的识别模型，将识别文字送入评分流程，再依次播报追问或后续正题。单次回答最多 90 秒，上传上限较低时自动缩短。在无语音或转写失败时检查能否重新作答。
4. 结束后主动打开练习报告，检查每道题的材料引用、转写文字和要点核对。摄像头画面由浏览器本地处理。语音服务与向量、会话服务各自绑定，后两者可使用独立的云端供应商。

## 开源来源

- 服务器朗读使用 MyShell.ai 的 MeloTTS 模型及 sherpa-onnx CPU 推理引擎。选用的中英双语 ONNX 模型附带 MIT 许可证，模型和许可证均由下载脚本按版本校验。[MeloTTS](https://github.com/myshell-ai/MeloTTS) · [ONNX 模型](https://huggingface.co/csukuangfj/vits-melo-tts-zh_en) · [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- 服务器转写使用 Vosk 的 `vosk-model-small-cn-0.22`。官方将该普通话小模型列为 Apache 2.0 许可。[Vosk](https://github.com/alphacep/vosk-api) · [官方模型清单](https://alphacephei.com/vosk/models)
- 浏览器的离线普通话合成使用本站随前端分发的 Spellophone / eSpeak NG，遵循 GPL-3.0-or-later。前端随包提供许可证、对应版本的源码和构建说明入口。[组件许可与源码指引](../../frontend/public/practice-voice/NOTICE.txt)
