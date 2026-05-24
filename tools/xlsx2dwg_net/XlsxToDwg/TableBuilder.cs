using System;
using System.Collections.Generic;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace XlsxToDwg;

/// <summary>
/// 两遍法核心构建类。
/// Pass 1: BuildMegaTable — 构建一个 mega-Table（不 append 到数据库），用 GenerateLayout() 测量真实行高。
/// Pass 2: 基于 SplitIntoChunks 切分后，由调用方创建独立 Table 实体。
/// </summary>
public static class TableBuilder
{
    /// <summary>
    /// 从 raw sections 构建 mega-table（不 append 到数据库）。
    /// 文本段：每行 1 cell at col 0，全列合并，不设行高（自动计算）。
    /// 数据段：cells 直接搬入，设行高为 xlsx 值。
    /// </summary>
    public static (Table table, List<bool> rowIsText, int maxNcols) BuildMegaTable(
        XlsxDocument doc,
        ObjectId tableStyleId,
        ObjectId textStyleId,
        ObjectId boldTextStyleId,
        double defaultTextHeight)
    {
        // 1. 找到 max_ncols（从非 borderless 的数据表段）
        int maxNcols = 1;
        foreach (var sec in doc.Sections)
        {
            if (!sec.Borderless)
                maxNcols = Math.Max(maxNcols, sec.NCols);
        }

        // 2. 构建全局 col_widths 数组
        List<double> colWs = BuildGlobalColWidths(doc, maxNcols);

        // 3. 遍历 sections，构建 cells/merges/row_heights/rowIsText
        var allCells = new List<CellInfo>();
        var allMerges = new List<MergeInfo>();
        var allRowHeights = new List<double>();
        var rowIsText = new List<bool>();
        int rowOffset = 0;

        foreach (var sec in doc.Sections)
        {
            int secNrows = sec.NRows;

            if (sec.Borderless)
            {
                int mid = maxNcols / 2;
                for (int ri = 0; ri < secNrows; ri++)
                {
                    allRowHeights.Add(0);
                    rowIsText.Add(true);

                    var matching = new List<CellInfo>();
                    foreach (var c in sec.Cells)
                    {
                        if (c.Row == ri) matching.Add(c);
                    }

                    if (matching.Count == 0) continue;

                    if (sec.NCols <= 1)
                    {
                        allCells.Add(new CellInfo
                        {
                            Row = rowOffset + ri, Col = 0,
                            Text = matching[0].Text, TextHeight = matching[0].TextHeight,
                            Alignment = matching[0].Alignment, Bold = matching[0].Bold
                        });
                        allMerges.Add(new MergeInfo
                        {
                            R1 = rowOffset + ri, C1 = 0,
                            R2 = rowOffset + ri, C2 = maxNcols - 1
                        });
                    }
                    else
                    {
                        bool isTitleRow = false;
                        foreach (var m in sec.Merges)
                            if (m.R1 == ri && m.C2 >= sec.NCols - 1) { isTitleRow = true; break; }

                        if (isTitleRow)
                        {
                            allCells.Add(new CellInfo
                            {
                                Row = rowOffset + ri, Col = 0,
                                Text = matching[0].Text, TextHeight = matching[0].TextHeight,
                                Alignment = matching[0].Alignment, Bold = matching[0].Bold
                            });
                            allMerges.Add(new MergeInfo
                            {
                                R1 = rowOffset + ri, C1 = 0,
                                R2 = rowOffset + ri, C2 = maxNcols - 1
                            });
                        }
                        else
                        {
                            foreach (var c in matching)
                            {
                                int mc = c.Col == 0 ? 0 : mid;
                                allCells.Add(new CellInfo
                                {
                                    Row = rowOffset + ri, Col = mc,
                                    Text = c.Text, TextHeight = c.TextHeight,
                                    Alignment = c.Alignment, Bold = c.Bold
                                });
                                allMerges.Add(new MergeInfo
                                {
                                    R1 = rowOffset + ri, C1 = mc,
                                    R2 = rowOffset + ri, C2 = c.Col == 0 ? mid - 1 : maxNcols - 1
                                });
                            }
                        }
                    }
                }
            }
            else
            {
                // 数据表段：cells 直接搬入
                foreach (var c in sec.Cells)
                {
                    allCells.Add(new CellInfo
                    {
                        Row = c.Row + rowOffset,
                        Col = c.Col,
                        Text = c.Text,
                        TextHeight = c.TextHeight > 0 ? c.TextHeight : defaultTextHeight,
                        Alignment = c.Alignment,
                        Bold = c.Bold
                    });
                }
                foreach (var m in sec.Merges)
                {
                    allMerges.Add(new MergeInfo
                    {
                        R1 = m.R1 + rowOffset, C1 = m.C1,
                        R2 = m.R2 + rowOffset, C2 = m.C2
                    });
                }

                for (int ri = 0; ri < secNrows; ri++)
                {
                    double rh = ri < sec.RowHeights.Count ? sec.RowHeights[ri] : 500;
                    allRowHeights.Add(rh);
                    rowIsText.Add(false);
                }
            }

            rowOffset += secNrows;
        }

        // 4. 创建 Table 对象（不 append）
        int totalRows = allRowHeights.Count;
        var table = new Table();
        table.SetSize(totalRows, maxNcols);
        table.TableStyle = tableStyleId;
        table.SuppressRegenerateTable(true);
#pragma warning disable CS0618
        table.IsTitleSuppressed = true;
#pragma warning restore CS0618

        // 设列宽
        for (int c = 0; c < maxNcols; c++)
        {
            double w = c < colWs.Count ? colWs[c] : 1000;
            if (w > 0)
                table.Columns[c].Width = w;
        }

        // 设行高（0 表示让 AutoCAD 自动计算）
        for (int r = 0; r < totalRows; r++)
        {
            double h = allRowHeights[r];
            if (h > 0)
                table.Rows[r].Height = h;
            // h == 0 时不设，让 AutoCAD 自动计算
        }

        // 填 cells
        foreach (var cell in allCells)
        {
            if (cell.Row < 0 || cell.Row >= totalRows || cell.Col < 0 || cell.Col >= maxNcols)
                continue;
            var tc = table.Cells[cell.Row, cell.Col];
            if (!string.IsNullOrEmpty(cell.Text))
                tc.TextString = cell.Text;
            double th = cell.TextHeight > 0 ? cell.TextHeight : defaultTextHeight;
            tc.TextHeight = th;
            tc.Alignment = (CellAlignment)cell.Alignment;
            if (cell.Bold && boldTextStyleId != ObjectId.Null)
                tc.TextStyleId = boldTextStyleId;
        }

        // 合并
        foreach (var merge in allMerges)
        {
            try
            {
                if (merge.R1 < 0 || merge.R2 >= totalRows ||
                    merge.C1 < 0 || merge.C2 >= maxNcols ||
                    merge.R1 > merge.R2 || merge.C1 > merge.C2)
                    continue;
                var range = CellRange.Create(table, merge.R1, merge.C1, merge.R2, merge.C2);
                table.MergeCells(range);
            }
            catch { }
        }

        // 生成布局（测量行高）
        table.GenerateLayout();

        return (table, rowIsText, maxNcols);
    }

    /// <summary>
    /// 从 GenerateLayout() 后的 Table 读取实际行高。
    /// 对于未设高度的行（文本行），AutoCAD 会自动计算。
    /// </summary>
    public static List<double> MeasureRowHeights(Table table, int nRows)
    {
        var heights = new List<double>(nRows);
        for (int r = 0; r < nRows; r++)
        {
            heights.Add(table.Rows[r].Height);
        }
        return heights;
    }

    /// <summary>
    /// 按列高切分为 chunks，每个 chunk 高度 <= maxColHeight。
    /// 返回 (startRow, endRow) 列表。
    /// </summary>
    public static List<(int startRow, int endRow)> SplitIntoChunks(
        List<double> heights, double maxColHeight)
    {
        var chunks = new List<(int, int)>();
        int chunkStart = 0;
        double chunkH = 0;

        for (int ri = 0; ri < heights.Count; ri++)
        {
            double rh = heights[ri];
            if (rh <= 0) rh = 300; // 安全下限
            if (chunkH + rh > maxColHeight && ri > chunkStart)
            {
                chunks.Add((chunkStart, ri - 1));
                chunkStart = ri;
                chunkH = rh;
            }
            else
            {
                chunkH += rh;
            }
        }
        chunks.Add((chunkStart, heights.Count - 1));

        return chunks;
    }

    /// <summary>
    /// 构建全局列宽数组。
    /// 策略：从 doc.ColWidths 取前 maxNcols 列（如果有），缩放到 Layout.COL_W。
    /// 如果 doc.ColWidths 为空，则均分。
    /// </summary>
    internal static List<double> BuildGlobalColWidths(XlsxDocument doc, int maxNcols)
    {
        // doc.ColWidths 可能是 Python 端输出的全局缩放后列宽
        // 如果有就用，没有就均分
        if (doc.ColWidths != null && doc.ColWidths.Count >= maxNcols)
        {
            var cw = new List<double>(maxNcols);
            double total = 0;
            for (int i = 0; i < maxNcols; i++)
            {
                double w = doc.ColWidths[i];
                cw.Add(w);
                total += w;
            }
            if (total > 0)
            {
                double scale = Layout.COL_W / total;
                for (int i = 0; i < cw.Count; i++)
                    cw[i] = Math.Round(cw[i] * scale, 1);
            }
            return cw;
        }

        // 均分
        double each = Layout.COL_W / maxNcols;
        var result = new List<double>(maxNcols);
        for (int i = 0; i < maxNcols; i++)
            result.Add(Math.Round(each, 1));
        return result;
    }
}
