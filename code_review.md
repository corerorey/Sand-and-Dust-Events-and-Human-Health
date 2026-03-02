# Code Review: Sand-and-Dust-Storms-and-Human-Health

**审查范围:** 20+ Python 源文件, 完整的 README  
**审查日期:** 2026-02-26

---

## Table of Contents
1. [Overall Assessment](#Overall-Assessment)
2. [🔴 Critical Issues](#🔴-Critical-Issues)
3. [🟡 Significant Issues](#🟡-Significant-Issues)
4. [🟠 Moderate Issues](#🟠-Moderate-Issues)
5. [🔵 Minor Issues](#🔵-Minor-Issues)
6. [📋 Summary Table](#📋-Summary-Table)
7. [👍 Positive Highlights](#👍-Positive-Highlights)
8. [Recommended Priority Actions](#Recommended-Priority-Actions)
9. [🚀 Future Plan](#🚀-未来计划-future-plan-基于项目路线图)

---
## Overall Assessment

这是一个**well-structured research project**，具有明确的科学焦点：将 sand/dust storm events 与 human health outcomes 联系起来。代码库涵盖了 data acquisition、preprocessing、event detection、卫星 collocation 以及 spatial visualization — 为 epidemiological analysis 奠定了坚实的基础。

**Strengths:**
- README 中记录了清晰的 scientific methodology
- 完整的数据管道 data pipeline：从原始数据 → 清洗后的 NetCDF → 事件检测 → 对齐的输出
- 很好地使用了 numpy `memmap` 进行内存高效的 NetCDF 构建
- 设计良好的卫星处理管道 (Himawari HSD → BT → Dust RGB)
- 数据下载器中具有健壮的 error handling 和 retry logic

**Areas for improvement** 按严重程度详细列出如下。

---

## 🔴 Critical Issues

### 1. Hardcoded Credentials

> **CAUTION:** Earthdata 凭证在 **4 个文件**中以纯文本形式硬编码，并已提交到 Git 仓库。

| File | Location |
|------|----------|
| [data_prep/merra-2/fetch2.py](data_prep/merra-2/fetch2.py#L38-L39) | `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` (lines 38–39) |
| [data_prep/merra-2/fetch_data.py](data_prep/merra-2/fetch_data.py#L18-L19) | 相同的凭证 (Same credentials) |
| [data_prep/merra-2/fetchch.py](data_prep/merra-2/fetchch.py#L18-L19) | 相同的凭证 (Same credentials) |
| [data_prep/merra-2/openfet.py](data_prep/merra-2/openfet.py#L17-L18) | 相同的凭证 (Same credentials) |

**Recommendation:**
- **立即 rotate** 您 Earthdata 账户的密码
- 使用 environment variables (`os.environ["EARTHDATA_USERNAME"]`) 或 `.env` 文件（配合 `.gitignore`）
- [openfet.py](data_prep/merra-2/openfet.py) 中的 `ensure_netrc()` 方法方向是对的 — 但也不要在那里硬编码凭证
- 将 `*.env` 和 `_netrc` 添加到 `.gitignore` 中

### 2. 缺少 `.gitignore` 文件

缺少 `.gitignore` 文件来防止敏感文件、输出 artifacts 或 IDE 元数据被提交。这直接加剧了上述凭证泄漏的问题。

**推荐的 `.gitignore` 内容:**

```gitignore
# Credentials & secrets
.env
_netrc
.netrc

# Python cache
__pycache__/
*.pyc
*.pyo

# IDE
.idea/
.vscode/

# Partial downloads
*.part

# Generated data outputs
_tmp_*/
out_*/
nc_out/
downloads_*/
```

---

## 🟡 Significant Issues

### 3. MERRA-2 脚本中大量的 Code Duplication

四个脚本 ([fetch2.py](data_prep/merra-2/fetch2.py), [fetch_data.py](data_prep/merra-2/fetch_data.py), [fetchch.py](data_prep/merra-2/fetchch.py), [openfet.py](data_prep/merra-2/openfet.py)) 包含 **heavily duplicated logic**:

| Duplicated Component | Files |
|---------------------|-------|
| URL 重定向 + auth handler | `fetch2.py`, `fetchch.py`, `fetch_data.py` |
| `filename_from_url()` | `fetch2.py`, `fetchch.py` |
| `read_lanzhou_aq()` + `_parse_lanzhou_date()` | `fetch2.py`, `openfet.py` |
| `build_daily_mean_table()` | `fetch2.py`, `openfet.py` |
| `sniff_delimiter()` | `fetch2.py`, `openfet.py` |
| `normalize_lon_for_ds()` | `fetch2.py`, `fetch_data.py`, `openfet.py` |
| Event detection logic | 所有三个 — 但有 **3 种不同的实现** |

**Impact:** Bug fixes 必须在 3–4 个地方应用；脚本之间不同的行为很容易被忽略。

**Recommendation:** 将共享的 utilities 提取到一个 `data_prep/merra-2/merra2_utils/` 包中:
- `auth.py` — 凭证加载、重定向处理、下载函数
- `event_detection.py` — 统一的事件检测，具有可配置的阈值
- `io_utils.py` — CSV sniffing、日期解析、兰州 AQ 读取

### 4. CNEMC 脚本之间重复的函数

[prepro.py](data_prep/cnemc_site_data/prepro.py) 和 [build_documento_nc.py](data_prep/cnemc_site_data/build_documento_nc.py) 共享几乎相同的 `parse_mixed_date()`, `discover_data_dirs()`, `path_has_data_segment()` 版本，以及相同的正则表达式模式 (`DATA_DIR_RE`, `DAILY_FILE_RE`)。

**Recommendation:** 将共享的 CNEMC utilities 提取到 `data_prep/cnemc_site_data/cnemc_common.py` 中。

### 5. 用于 Mapbase 导入的 `sys.path` Manipulation

多个文件在运行时将 `mapbase` 目录插入到 `sys.path` 中:
- [data_prep/cnemc_site_data/vis.py](data_prep/cnemc_site_data/vis.py) (line 108)
- [data_prep/himawari/hima.py](data_prep/himawari/hima.py) (line 23)
- [data_prep/merra-2/plot_event16_mean_integral_2x3.py](data_prep/merra-2/plot_event16_mean_integral_2x3.py) (line 49)
- [data_prep/merra-2/plot_event16_spatial_heatmaps.py](data_prep/merra-2/plot_event16_spatial_heatmaps.py) (line 46)

**Recommendation:** 通过添加 `__init__.py` 将 `data_prep/mapbase/` 转换为可导入的 proper package，或者添加根级别的 `pyproject.toml`，以便该包可以使用 `pip install -e .` 安装。

---

## 🟠 Moderate Issues

### 6. 硬编码的 Windows Absolute Paths

几乎每个脚本都使用硬编码的 `C:\DOCUMENTO\...` 路径:

| File | Hardcoded Path Variable |
|------|------------------------|
| `build_documento_nc.py` | `INPUT_ROOT` |
| `prepro.py` | `DEFAULT_SCAN_ROOT` |
| `vis.py` | `NC_PATH` |
| `hima.py` | `DEFAULT_DAT_ROOT` |
| `fetch2.py` | `LOCAL_NC_GLOB` |
| `fetch_data.py` | `WEBCRAWLER_DIR`, `OTF_URL_LIST_FILE` |
| `fetchch.py` | `TXT_PATH` |
| `openfet.py` | `LINKLIST_PATH`, `LANZHOU_CSV_PATH` |

**Impact:** 如果不单独编辑每个脚本，则无法在另一台机器上运行该项目。

**Recommendation:**
- 对于所有数据根路径，使用中央 `config.yaml` 或 `.env` 文件
- 或在路径可预测的地方使用通过 `Path(__file__).resolve().parents[N]` 计算的相对路径

### 7. 三种不同的 Event Detection Implementations

| File | Function | Approach |
|------|----------|----------|
| `fetch2.py` | `detect_events()` | 双重标准 (主要 + 次要)，基于 segment 加 gap-merge |
| `fetch_data.py` | `detect_events_hourly()` | 单一标准，基于 flag，使用排他性的末尾时间戳 (exclusive end timestamp) |
| `openfet.py` | `detect_events()` | 单一标准，`while` 循环扫描器，使用包含性的末尾 (inclusive end) |

它们使用**不同的 boundary conventions**（包含 vs 排除）和**不同的 merge strategies**。对相同数据运行 `fetch_data.py` vs `fetch2.py` 可能会产生不同的事件计数，这是一个 reproducibility 问题。

**Recommendation:** 合并为一个参数化的 `detect_events()` 函数，并带有可选的双重标准模式。

### 8. Mixed Language in Code Comments

代码注释和错误消息中混合使用了英语和中文:
- `fetch_data.py`: `# 事件识别（小时级）`, `raise RuntimeError("没有提取到小时序列")`
- `fetchch.py`: `# 去重但保序`, 中文错误消息
- `openfet.py`: `# 你需要改的配置（只改这里）`, `# 兰州坐标`

**Recommendation:** 在用于发布或协作的代码库中，将所有注释、docstrings 和错误消息统一为英语。

### 9. 没有 Dependencies Management

没有 `requirements.txt`, `pyproject.toml` 或 `environment.yml`。该项目依赖于大量的外部包:

```
numpy, pandas, xarray, netCDF4, matplotlib, cartopy, scipy,
shapely, requests, beautifulsoup4, lxml, deep_translator (optional),
certifi, urllib3
```

**Recommendation:** 创建一个至少固定主版本的 `requirements.txt`，或者创建一个用于完全可重复性的 `pyproject.toml`。

---

## 🔵 Minor Issues

### 10. 仓库中遗留的 Debug Script

[data_prep/himawari/_tmp_bt_check.py](data_prep/himawari/_tmp_bt_check.py) 是一个一次性的调试脚本，变量名很简单 (`b`, `L`, `off`)，也没有 docstrings。没有害处，但它使仓库变得杂乱。

### 11. `prepro.py` 混合了脚本和 Notebook Cell 模式

[prepro.py](data_prep/cnemc_site_data/prepro.py) 的 355–409 行包含一个 `#%%` cell 块，运行了位于 `if __name__ == "__main__"` 保护**之外**的绘图代码。如果这个文件作为模块被导入，该代码将被无意中执行。

### 12. Bare `except` Blocks

多个文件捕获异常时过于宽泛:
- `build_documento_nc.py` line 751 — `mem._mmap.close()` 包装在一个裸 `except` 中
- `fetch_data.py` lines 517–519 — `read_any_csv()` 默默地吞下所有 12 种编码/分隔符组合的错误

**Recommendation:** 捕获特定的异常类型，并使用 `logging.warning()` 而不是静默抑制。

### 13. `openfet.py` 在没有警告的情况下 overwrites `~/_netrc`

`ensure_netrc()` 函数 (line 56) 无条件地**overwrites** 具有单个机器条目的 `~/_netrc` 文件。如果该文件已经包含其他服务的凭证，它们将被静默丢失。

### 14. 输出文件名 Collisions

`fetch2.py` 和 `openfet.py` 都将具有相同命名的输出文件 (`hourly_timeseries_with_event_mark.csv`, `dust_events_summary.csv`) 写入不同的输出目录。这会导致对哪个是权威的“events summary”产生混淆。

### 15. 没有 Unit Tests

该项目没有测试套件 (test suite)。考虑到事件检测、插值逻辑和坐标转换的复杂性，即使是一小部分单元测试也能显著提高重构期间的信心。

---

## 📋 Summary Table

| Severity | Issue | Estimated Effort |
|----------|-------|-----------------|
| 🔴 Critical | 4 个文件中硬编码的凭证 | Low — 使用 env vars |
| 🔴 Critical | 缺少 `.gitignore` | Low — 创建文件 |
| 🟡 Significant | MERRA-2 代码重复 (4 个脚本) | Medium — 提取 utils 模块 |
| 🟡 Significant | CNEMC 脚本中重复的代码 | Medium — 创建 `cnemc_common.py` |
| 🟡 Significant | 对 mapbase 的 `sys.path` hacking | Medium — proper packaging |
| 🟠 Moderate | 硬编码的 Windows 绝对路径 | Medium — 集中式 config |
| 🟠 Moderate | 3 种发散的事件检测引擎 | Medium — unify |
| 🟠 Moderate | 混合语言的注释 | Low — 翻译为英语 |
| 🟠 Moderate | 缺少依赖管理 | Low — 创建 `requirements.txt` |
| 🔵 Minor | 仓库中的调试脚本 | Trivial — 删除或移动 |
| 🔵 Minor | `__main__` protection 外的 Notebook cell | Low — 包装在函数中 |
| 🔵 Minor | 裸 `except` 块 | Low — 缩小异常类型 |
| 🔵 Minor | overwrites `_netrc` 的风险 | Low — 写入前检查 |
| 🔵 Minor | 输出文件名 Collisions | Low — 为输出添加命名空间 |
| 🔵 Minor | 没有 unit tests | High effort, high value |

---

## 👍 Positive Highlights

| Aspect | Details |
|--------|---------|
| **Scientific rigor** | `build_documento_nc.py` 和 `prepro.py` 中的年度数据失效 + 保守的 short-gap interpolation 在方法论上是合理的 |
| **Satellite processing** | `hima.py` 具有干净、类型良好的 pipeline: HSD 二进制 → BT → Dust RGB → 云掩膜 (cloud mask) → 站点 collocation |
| **Memory management** | `build_documento_nc.py` 使用 `numpy.memmap` 来进行内存高效的一整年 NetCDF 构建 |
| **Mapbase 库** | `cnmap.py` (~1900 lines) 是一个全面的 cartographic toolkit，包括用于省级地图的 DSATUR 图着色算法 |
| **Robust downloading** | `fetchch.py` / `fetch2.py` 处理 URS 重定向链，部分下载恢复以及 HTML 响应检测 |
| **Translation pipeline** | `build_documento_nc.py` 对于中国站点名称有一个 3 层的翻译 fallback (deep_translator → 直接 API → 基于规则) |
| **Web crawler** | `zonghe.py` 具有基于 exponential-backoff 的干净重试逻辑和按标准化日期键对双数据源 (weather + AQI) 进行逐月合并的能力 |

---

## Recommended Priority Actions

1. **Immediately** rotate Earthdata 密码并从所有脚本中删除凭证
2. 添加 `.gitignore` (防止将来的泄漏) 和 `requirements.txt` (提高 reproducibility)
3. 将共享的 MERRA-2 utilities 提取到单个模块中，以消除 4 处的重复
4. 将三种事件检测的实现统一成一个参数化的函数
5. 将所有硬编码的数据路径移至中央配置文件

---

## 🚀 Future Plan (基于项目路线图)

基于 README 中概述的战略目标，在解决基础性的代码库问题之后，项目应无缝过渡到其核心的科学与分析阶段。我们已经为阶段 1 到阶段 4 奠定了基础基础设施：

### Phase 1: Establish Health & Exposure Data Harmonization [基础架构已完成]
- **Finalize Spatiotemporal Alignment:** 制度化每日/每周 MERRA-2 暴露指标与 CNEMC 站点数据的合并管道 (详见 `data_prep/exposure_engineering/event_builder.py`)。
- **Dust/Non-Dust PM Separation:** 整合了基于 proxy-based 的方法，用于区分沙尘驱动的 PM 和背景人为 PM (详见 `data_prep/exposure_engineering/dust_separation.py`)。

### Phase 2: Implement Health-Risk Modeling [基础架构已完成]
- **GAM & DLNM 构建:** 构建了通过 `pygam` 建立 Generalized Additive Models (GAM) 和在 R 中建立 Distributed Lag Nonlinear Models (DLNM) 的基线脚本 (详见 `health_modeling/gam_baseline.py` 和 `dlnm_baseline.R`)。
- **Multi-Site Analysis Infrastructure:** 准备了 random-effects meta-analysis 模块，以汇集各个特定城市的健康风险 (详见 `health_modeling/meta_analysis.py`)。

### Phase 3: Integrate Causal Inference & Machine Learning [基础架构已完成]
- **Causal Frameworks:** 设计了结构化的 DoWhy 基线，使用 Directed Acyclic Graphs (DAGs) 和 adjustment sets，将 weather confounders 与 PM 健康影响分开 (详见 `health_modeling/causal_baseline.py`)。
- **ML Risk Classifiers:** 配置了一个快速的 `TabPFN` 基线分类器，将健康/暴露变量映射为 高/低 风险阈值 (详见 `health_modeling/tabpfn_baseline.py`)。

### Phase 4: Develop Decision Support Tools [基础架构已完成]
- **Rehearsal Learning Implementation:** 起草了一个 skeleton 映射器，将对齐的时空与干预数据输入到 Grad-RH 和 AUF-MICNS 算法所需的特定 `(X, Z, Y)` 张量格式中 (详见 `health_modeling/decision_support_skeleton.py`)。

### Phase 5: Comprehensive Evaluation & Extension [待完成]
- **Model Diagnostics:** 形式化暴露评估指标 (如基于 AERONET 的 hit rate) 和健康模型的稳健性检查 (如 residual autocorrelation，overdispersion)。
- **Data Scaling:** 有效地将数据来源扩展到 Event 4 和 Event 16 快照之外，并将这些基线模型应用于过去十年的历史趋势中。
