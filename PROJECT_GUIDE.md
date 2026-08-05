# Multiagent A2A — hướng dẫn chạy project

Project đã tách notebook ban đầu thành package Python có contracts, data ports,
agents, Qwen gateway, verifier, artifact QA, CLI và test. Notebook vẫn là entrypoint
thuận tiện trên Kaggle nhưng không chứa lại business logic.

## Cấu trúc

```text
src/multiagent_a2a/
├── agents/          # 6 agent và coordinator
├── application/     # composition root, pipeline
├── artifacts/       # atomic JSON, metadata, QA và ZIP
├── data/            # case loader, Olist repository
├── domain/          # tiền, timestamp, deterministic policy
├── llm/             # parser và local-only Qwen gateway
├── observability/   # JSONL trace
├── cli.py
├── config.py
├── constants.py
├── contracts.py
└── ports.py
tests/               # unit + official integration
multi_agent_ecommerce_dispute_qwen3.ipynb
```

## Chạy local bằng fallback

Không cần Transformers, GPU hay model:

```powershell
$env:PYTHONPATH = "src"
python -m multiagent_a2a run --data-dir data --input-dir input --work-root . --no-llm
```

Kết quả gồm `output/`, `submission.zip`, `trace.jsonl`, `metadata.json` và mirror
trong `logging/`.

## Chạy trên Kaggle với Qwen3-8B

Attach ba asset vào notebook:

1. repo này (phải có `pyproject.toml` và `src/multiagent_a2a`);
2. data/cases (các CSV cần thiết và `EC_001.json` … `EC_050.json`);
3. Qwen3-8B dưới dạng Kaggle Model/Dataset đã có sẵn trên `/kaggle/input`.

Bật GPU rồi Run All notebook. Nếu auto-discovery có nhiều asset giống nhau, điền
`MODEL_PATH` trong cell cấu hình. Có thể điền `DATA_DIR` và `INPUT_DIR`; để `None`
thì runner tự tìm marker. Artifacts nằm tại `/kaggle/working`.

Project không tự chạy `pip`, không gọi Hugging Face Hub và không tải model. Nếu
Kaggle image thiếu optional dependencies, backend ghi rõ lý do vào metadata rồi
dùng rule fallback. Muốn dùng Qwen, hãy chọn Kaggle image đã có dependency hoặc
attach/install wheel theo thao tác chủ động của bạn.

## CLI

```text
python -m multiagent_a2a run \
  --data-dir <dir> --input-dir <dir> --work-root <dir> \
  [--model-path <attached-qwen3-8b>] [--llm|--no-llm]

python -m multiagent_a2a validate \
  --data-dir <dir> --input-dir <dir> \
  --output-dir <dir> --zip-path <submission.zip>
```

`validate` dựng lại canonical output từ CSV/case bằng rule engine rồi so từng
field với file trên disk; evidence hoặc số tiền giả sẽ làm lệnh thất bại.

Thứ tự cấu hình là CLI → environment → auto-discovery. Các environment hữu ích:

| Biến | Ý nghĩa |
|---|---|
| `EC_DATA_DIR` | thư mục chứa 4 Olist CSV cần thiết |
| `EC_INPUT_DIR` | thư mục chứa 50 case JSON |
| `EC_WORK_ROOT` | nơi ghi artifacts |
| `EC_ENABLE_LLM` | thử dùng local model, mặc định `1` |
| `EC_FORCE_RULE_FALLBACK` | ép rules, ưu tiên cao nhất |
| `QWEN_MODEL_PATH` | Qwen3-8B đã có sẵn |
| `QWEN_MAX_NEW_TOKENS` | giới hạn output, mặc định 96 |
| `EC_MIRROR_LOGGING` | mirror trace/metadata, mặc định `1` |
| `EC_STRICT_OFFICIAL_ASSERTIONS` | bật golden 50-case QA |

Không có biến cho phép download model.

## Test

```powershell
$env:PYTHONPATH = "src"
pytest
```

Test integration luôn ép deterministic fallback, dùng thư mục tạm và không import
model runtime. GPU smoke test là optional và chỉ chạy khi người dùng chủ động cung
cấp local model path.

## Phụ thuộc

- Core: Python 3.10+, pandas 2.x.
- Optional Qwen: Transformers 4.51+, Accelerate, BitsAndBytes và CUDA-compatible
  PyTorch do môi trường Kaggle cung cấp.

`requirements-qwen.txt` chỉ là manifest tham khảo; source code và notebook không
tự cài nó.
