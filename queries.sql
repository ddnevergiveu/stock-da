-- Read-only examples for the normalized market_data table.
-- DateTime uses YYYY-MM-DD HH:MM:SS; TickVolume is a tick count, not traded volume.
-- 每根记录的百分比涨幅（不是未来收益，也不假设所有相邻记录恰好相隔15分钟）
SELECT *, 100.0 * (Close - Open) / Open AS GainPct
FROM market_data
ORDER BY GainPct DESC
LIMIT 5;

-- 每小时平均 Tick 数，不是真实交易量
SELECT
  SUBSTR(DateTime, 1, 13) AS HourBlock,
  AVG(TickVolume) AS AvgTicks
FROM market_data
GROUP BY HourBlock
ORDER BY HourBlock;

--  收盘价高于 23000 的记录
SELECT DateTime, Close
FROM market_data
WHERE Close > 23000
ORDER BY Close DESC;

-- 涨、跌、平的时间段统计
SELECT
  CASE
    WHEN Close > Open THEN 'Increase'
    WHEN Close < Open THEN 'Decrease'
    ELSE 'No Change'
  END AS Movement,
  COUNT(*) AS Count
FROM market_data
GROUP BY Movement;

--  每天最高价
SELECT
  SUBSTR(DateTime, 1, 10) AS Date,
  MAX(High) AS MaxHigh
FROM market_data
GROUP BY Date
ORDER BY Date;

-- 各星期平均收盘价（0为星期天）
SELECT
  STRFTIME('%w', DateTime) AS Weekday,
  AVG(Close) AS AvgClose
FROM market_data
GROUP BY Weekday
ORDER BY Weekday;

-- 相对振幅最大的时段（High - Low）/ Open
SELECT
  DateTime,
  100.0 * (High - Low) / Open AS RangePct
FROM market_data
ORDER BY RangePct DESC
LIMIT 10;

-- 每天首条和末条记录的 TickVolume
WITH RankedData AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY SUBSTR(DateTime, 1, 10) ORDER BY DateTime ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY SUBSTR(DateTime, 1, 10) ORDER BY DateTime DESC) AS rn_desc
  FROM market_data
)
SELECT DateTime, TickVolume
FROM RankedData
WHERE rn_asc = 1 OR rn_desc = 1
ORDER BY DateTime;

--  收盘价区间分布
SELECT
  CASE
    WHEN Close < 22800 THEN '< 22800'
    WHEN Close < 22900 THEN '22800 - 22900'
    WHEN Close < 23000 THEN '22900 - 23000'
    WHEN Close < 23100 THEN '23000 - 23100'
    ELSE '>= 23100'
  END AS PriceRange,
  COUNT(*) AS Count
FROM market_data
GROUP BY PriceRange
ORDER BY PriceRange;

--  判断是否有“突刺”（当前收盘价大于上一个 high）
WITH Lagged AS (
  SELECT
    DateTime,
    Close,
    LAG(High) OVER (ORDER BY DateTime) AS PrevHigh
  FROM market_data
)
SELECT *
FROM Lagged
WHERE Close > PrevHigh;
