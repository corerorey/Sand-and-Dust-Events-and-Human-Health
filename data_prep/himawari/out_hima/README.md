# Himawari 输出说明（2021-03-16 04:00 UTC）

本文档说明 `out_hima/` 下每个输出图和表的含义，帮助你快速判断它们能说明什么、不能说明什么。

## 1. 本次处理的对象与流程

- 时间切片：`2021-03-16 04:00 UTC`（中国时区 UTC+8 为 `2021-03-16 12:00`）
- 波段：Himawari-8 AHI `B11/B13/B15`
- 主流程：`DN -> Radiance -> Brightness Temperature (BT) -> Dust RGB/BTD -> Conservative 云掩膜 -> 站点 PM10 对齐`
- 空间范围：`[70E, 145E, 5N, 60N]`（中国及周边）

## 2. 结果总览：这些图是否“定性”？

- 绝大多数“图像解译”结论是**定性的**（形态、区域、相对强弱）。
- BT 图（单位 K）本身是物理量，属于**可量化变量**，但“是否尘暴”仍需结合阈值与掩膜。
- 站点对齐 CSV 给的是**半定量到定量**统计（相关系数、命中率等），比单图更接近可验证证据。

## 3. 新流程（论文风格）输出文件解释

### `hima_b11_bt_map.png`

![B11 BT](./hima_b11_bt_map.png)

- 做了什么：
  - 将 B11 由 DN 定标到亮温（K）并投影到经纬度底图。
- 说明什么：
  - 反映 8.6 μm 通道的热红外亮温空间结构。
  - 可辅助识别温度梯度与潜在地表/云区差异。
- 解读性质：
  - 变量本身定量（K），对“粉尘”判别是辅助定性。

### `hima_b13_bt_map.png`

![B13 BT](./hima_b13_bt_map.png)

- 做了什么：
  - B13 定标为亮温（K），经纬度可视化。
- 说明什么：
  - 10.4 μm 通道常用于云顶温度与热场结构参照。
  - 在本流程中也是 Dust RGB 的蓝通道基础量。
- 解读性质：
  - 定量变量，尘判识用途为辅助定性。

### `hima_b15_bt_map.png`

![B15 BT](./hima_b15_bt_map.png)

- 做了什么：
  - B15 定标为亮温（K），经纬度可视化。
- 说明什么：
  - 12.4 μm 通道与 B13 差分可突出矿物尘相关光谱特征。
- 解读性质：
  - 定量变量，尘判识用途为辅助定性。

### `hima_dust_rgb_paper_fixed_map.png`

![Dust RGB Fixed](./hima_dust_rgb_paper_fixed_map.png)

- 做了什么：
  - 采用固定范围的 Dust RGB 组合（论文风格）：
  - `R = BTD(15-13)`，范围 `[-6.7, 2.6]`
  - `G = BTD(13-11)`，范围 `[-0.5, 20]`
  - `B = BT13`，范围 `[261.2, 288.7]`
- 说明什么：
  - 用颜色组合突出疑似扬尘区域与背景差异。
  - 适合“快速识别可能尘带/尘源及其空间形态”。
- 解读性质：
  - 主要是定性判读图，不等于地面 PM10 浓度图。

### `hima_cloud_mask_conservative_map.png`

![Cloud Mask Conservative](./hima_cloud_mask_conservative_map.png)

- 做了什么：
  - 采用保守云掩膜（conservative）将可疑云区标记出来。
- 说明什么：
  - 哪些区域被认为“云影响较强，不宜直接用于尘判识”。
  - 后续 dust candidate 只在 clear 区内判定。
- 解读性质：
  - 规则型二值结果，偏保守，旨在减少云误判。

### `hima_dust_candidate_mask_map.png`

![Dust Candidate Mask](./hima_dust_candidate_mask_map.png)

- 做了什么：
  - 在 clear 区内按阈值条件生成疑似尘像元（binary mask）。
- 说明什么：
  - “可能有尘”的空间位置与范围。
  - 可用于与 MERRA 或站点进行时空比对。
- 解读性质：
  - 判别掩膜，偏定性/半定量，不是质量浓度反演。

### `hima_station_pm10_overlay_snapshot.png`

![Dust RGB + Station PM10 Snapshot](./hima_station_pm10_overlay_snapshot.png)

- 做了什么：
  - 底层显示 Dust RGB，叠加同小时中国站点 PM10 散点。
  - 对齐时刻：`2021-03-16 12:00`（本地时间）。
- 说明什么：
  - 卫星判识形态与地面 PM10 高值站点是否空间一致。
  - 可直观看“卫星信号与地面污染分布”是否大致同向。
- 解读性质：
  - 视觉对照图，偏定性；严谨比较看 CSV 统计。

## 4. 站点对齐表（比图更可验证）

### `hima_station_collocation_snapshot.csv`

- 每个站点一行，包含：
  - 站点 PM10、最近卫星像元位置与距离
  - 卫星 BT、BTD、RGB 归一化值、云标记、dust candidate 标记、DLI
- 作用：
  - 做站点级分析、筛选异常站点、回归/相关检验。

### `hima_station_collocation_summary.csv`

- 汇总指标（单时次）示例：
  - `n_sites_collocated`
  - `spearman_pm10_vs_btd15_13`
  - `spearman_pm10_vs_dli`
  - `high_pm10_p90_hit_rate`
- 作用：
  - 给出“卫星诊断量和站点 PM10 一致性”的定量摘要。

## 5. 旧版本输出（保留用于对比）

下列文件来自早期经验 proxy（DN 归一化）流程，建议作为对比参考，不作为当前主结果：

- `hima_b11_dn_map.png`
- `hima_b13_dn_map.png`
- `hima_b15_dn_map.png`
- `hima_dust_proxy_rgb_map.png`
- `hima_dust_proxy_score_mask_event_link.png`
- `hima_event_link_summary.csv`

这些结果更偏经验化，受场景归一化与云影响更明显。

## 6. 使用建议（科研解释口径）

- 先看 `hima_cloud_mask_conservative_map.png`，确认可判读区域。
- 再看 `hima_dust_rgb_paper_fixed_map.png` 与 `hima_dust_candidate_mask_map.png`，确定疑似尘区。
- 用 `hima_station_pm10_overlay_snapshot.png` 做空间一致性快速检查。
- 最后用 `hima_station_collocation_snapshot.csv` 和 `hima_station_collocation_summary.csv` 做定量支撑。

## 7. 关键限制

- 当前只有 `B11/B13/B15`，不是 6 通道完整 Dust RGB 方案。
- Dust RGB 和候选掩膜主要是“识别/筛查”工具，不直接等于地面浓度。
- 站点 PM10 与卫星热红外信号存在高度差异、时滞和边界层过程差异，出现偏差是常见现象。
