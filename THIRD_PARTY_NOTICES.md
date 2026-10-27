# 第三方内容与依赖许可说明（THIRD_PARTY_NOTICES）

本仓库代码以 MIT 许可证发布（见 [LICENSE](LICENSE)）。本文件说明仓库内
非本项目原创内容与运行依赖的许可证归属，避免与主许可证混淆。

## 仓库内内容

| 内容 | 来源 | 许可 |
|---|---|---|
| `samples/`、`corpus/` 下的样例论文与比对语料 | 本仓库脚本（`scripts/make_samples.py`、`scripts/make_check_corpus.py`）生成的合成内容 | 随本仓库以 MIT 发布 |
| README 中引用的开源项目（LLM-AES、EssayJudge、Pro_llm_correct、gpt_academic 等） | 各自原作者 | 版权归原作者所有，本仓库仅作方案致谢与出处标注 |

## 运行依赖的注意事项

各依赖库遵循其原始许可证，本仓库未修改、未再分发其代码。以下两项需要
使用者特别留意：

### PyMuPDF（AGPL-3.0 / 商业双许可）

`paper_grader/extract.py` 与 `paper_check` 的 PDF 解析依赖
[PyMuPDF](https://pymupdf.readthedocs.io/)。PyMuPDF 采用 **AGPL-3.0 与商业
许可双许可**：以本仓库为基础做分发或提供网络服务时，若保留 PyMuPDF
依赖，需满足 AGPL-3.0 的开源义务，或向 Artifex 购买商业许可。
仅在本机自用（不分发、不对组织外提供服务）通常不触发 AGPL 传染条款，
但请根据自身使用场景核对 [官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html)，
必要时可改用其他 PDF 解析库替换 `extract.py` 中的实现。

### 外部大模型服务

批改功能调用的外部服务（智谱 / DeepSeek / OpenAI 等）各自的服务条款、
数据处理与保留政策独立于本仓库许可证，使用前请自行查阅并确认。
