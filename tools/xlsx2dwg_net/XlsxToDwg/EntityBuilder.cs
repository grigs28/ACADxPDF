using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace XlsxToDwg;

public static class EntityBuilder
{
    private static void Log(string msg)
    {
        try
        {
            var path = Path.Combine(Path.GetTempPath(), "xlsx2dwg_cs.log");
            File.AppendAllText(path, $"[{DateTime.Now:HH:mm:ss.fff}] EB: {msg}\n", Encoding.UTF8);
        }
        catch { }
    }

    public static void AddMText(
        Transaction tr,
        BlockTableRecord modelSpace,
        SectionInfo sec,
        double defaultTextHeight,
        ObjectId textStyleId)
    {
        if (sec.Insert == null || sec.Insert.Count < 3)
            return;

        var mtext = new MText();
        mtext.Location = new Point3d(sec.Insert[0], sec.Insert[1], sec.Insert[2]);
        mtext.Contents = sec.Text ?? "";
        mtext.TextHeight = sec.TextHeight > 0 ? sec.TextHeight : defaultTextHeight;
        mtext.Width = sec.Width;
        mtext.TextStyleId = textStyleId;
        mtext.Attachment = sec.Attachment == "TopCenter"
            ? AttachmentPoint.TopCenter
            : AttachmentPoint.TopLeft;
        mtext.LineSpacingFactor = 1.2;

        modelSpace.AppendEntity(mtext);
        tr.AddNewlyCreatedDBObject(mtext, true);
    }

    public static void AddTable(
        Transaction tr,
        BlockTableRecord modelSpace,
        SectionInfo sec,
        double defaultTextHeight,
        ObjectId textStyleId,
        ObjectId boldTextStyleId,
        ObjectId tableStyleId)
    {
        if (sec.Insert == null || sec.Insert.Count < 3)
            return;
        if (sec.NRows <= 0 || sec.NCols <= 0)
            return;

        var table = new Table();
        table.SetSize(sec.NRows, sec.NCols);
        table.Position = new Point3d(sec.Insert[0], sec.Insert[1], sec.Insert[2]);
        table.TableStyle = tableStyleId;

        table.SuppressRegenerateTable(true);

#pragma warning disable CS0618
        if (sec.TitleSuppressed)
            table.IsTitleSuppressed = true;

        // 列宽
        for (int c = 0; c < sec.NCols; c++)
        {
            double w = GetCycledValue(sec.ColWidths, c);
            if (w > 0)
                table.Columns[c].Width = w;
        }

        // 行高
        for (int r = 0; r < sec.NRows; r++)
        {
            double h = GetCycledValue(sec.RowHeights, r);
            if (h > 0)
                table.Rows[r].Height = h;
        }
#pragma warning restore CS0618

        // 填充单元格
        double cellDefaultTh = sec.DefaultTextHeight > 0 ? sec.DefaultTextHeight : defaultTextHeight;
        foreach (var cell in sec.Cells)
        {
            if (cell.Row < 0 || cell.Row >= sec.NRows || cell.Col < 0 || cell.Col >= sec.NCols)
                continue;

            var tc = table.Cells[cell.Row, cell.Col];
            if (!string.IsNullOrEmpty(cell.Text))
                tc.TextString = cell.Text;

            double th = cell.TextHeight > 0 ? cell.TextHeight : cellDefaultTh;
            tc.TextHeight = th;

            tc.Alignment = (CellAlignment)cell.Alignment;

            // 加粗：使用 bold 文字样式
            if (cell.Bold && boldTextStyleId != ObjectId.Null)
                tc.TextStyleId = boldTextStyleId;
        }

        // 合并单元格（逐个 try-catch 跳过无效范围）
        foreach (var merge in sec.Merges)
        {
            try
            {
                if (merge.R1 < 0 || merge.R2 >= sec.NRows ||
                    merge.C1 < 0 || merge.C2 >= sec.NCols ||
                    merge.R1 > merge.R2 || merge.C1 > merge.C2)
                    continue;
                var range = CellRange.Create(table, merge.R1, merge.C1, merge.R2, merge.C2);
                table.MergeCells(range);
            }
            catch { /* 跳过无效合并 */ }
        }

        // GenerateLayout 必须在 AppendEntity 之前调用
        table.GenerateLayout();

        // 按行边框控制：row_borderless 行隐藏边框，数据行隐藏空单元格边框
        for (int r = 0; r < sec.NRows; r++)
        {
            bool hideBorders = sec.RowBorderless != null
                && r < sec.RowBorderless.Count && sec.RowBorderless[r];
            if (hideBorders)
            {
                for (int c = 0; c < sec.NCols; c++)
                {
                    try
                    {
                        var borders = table.Cells[r, c].Borders;
                        borders.Top.IsVisible = false;
                        borders.Bottom.IsVisible = false;
                        borders.Left.IsVisible = false;
                        borders.Right.IsVisible = false;
                    }
                    catch { }
                }
            }
            else
            {
                // 数据行：隐藏空单元格边框（处理 ncols < max_ncols 的尾列）
                for (int c = 0; c < sec.NCols; c++)
                {
                    try
                    {
                        var tc = table.Cells[r, c];
                        if (string.IsNullOrEmpty(tc.TextString))
                        {
                            var borders = tc.Borders;
                            borders.Top.IsVisible = false;
                            borders.Bottom.IsVisible = false;
                            borders.Left.IsVisible = false;
                            borders.Right.IsVisible = false;
                        }
                    }
                    catch { }
                }
            }
        }

        table.SuppressRegenerateTable(false);

        modelSpace.AppendEntity(table);
        tr.AddNewlyCreatedDBObject(table, true);
    }

    public static void AddTableFromChunk(
        Transaction tr,
        BlockTableRecord modelSpace,
        ObjectId tableStyleId,
        ObjectId textStyleId,
        ObjectId boldTextStyleId,
        double defaultTextHeight,
        List<double> colWidths,
        int startRow, int endRow,
        int headerStartRow, int headerEndRow,
        List<double> measuredHeights,
        List<CellInfo> allCells,
        List<MergeInfo> allMerges,
        List<bool> rowIsText,
        int maxNcols,
        Point3d insertPoint)
    {
        int dataRows = endRow - startRow + 1;
        int headerNRows = (headerStartRow >= 0) ? (headerEndRow - headerStartRow + 1) : 0;
        int totalRows = headerNRows + dataRows;
        if (totalRows <= 0 || maxNcols <= 0) return;

        // +1 行给 AutoCAD 的 Title Row（row 0），我们的内容从 row 1 开始
        // 这样 IsTitleSuppressed=true 隐藏 row 0（空的 Title Row），不影响我们的内容
        int tableRows = 1 + totalRows;

        var table = new Table();
        table.SetSize(tableRows, maxNcols);
        table.Position = insertPoint;
        table.TableStyle = tableStyleId;

        table.SuppressRegenerateTable(true);

#pragma warning disable CS0618
        table.IsTitleSuppressed = true;
#pragma warning restore CS0618

        // 列宽
        for (int c = 0; c < maxNcols; c++)
        {
            double w = c < colWidths.Count ? colWidths[c] : 1000;
            if (w > 0)
                table.Columns[c].Width = w;
        }

        // Title row (row 0) 设最小高度
        table.Rows[0].Height = 1;

        // 行高：表头行 row 1+，数据行 row (1+headerNRows)+
        for (int r = 0; r < headerNRows; r++)
        {
            int srcRow = headerStartRow + r;
            double h = (srcRow >= 0 && srcRow < measuredHeights.Count) ? measuredHeights[srcRow] : 300;
            if (h > 0)
                table.Rows[1 + r].Height = h;
        }
        for (int r = 0; r < dataRows; r++)
        {
            int srcRow = startRow + r;
            double h = (srcRow >= 0 && srcRow < measuredHeights.Count) ? measuredHeights[srcRow] : 300;
            if (h > 0)
                table.Rows[1 + headerNRows + r].Height = h;
        }

        // 构建边框查找表
        var borderMap = new Dictionary<(int, int), string>();
        foreach (var cell in allCells)
        {
            if (!string.IsNullOrEmpty(cell.Borders))
                borderMap[(cell.Row, cell.Col)] = cell.Borders;
        }

        // 行映射：mega-table srcRow → local table row (-1 if not in this chunk)
        // +1 offset: row 0 是被隐藏的 Title Row，我们的内容从 row 1 开始
        int MapRow(int srcRow)
        {
            if (headerStartRow >= 0 && srcRow >= headerStartRow && srcRow <= headerEndRow)
                return 1 + (srcRow - headerStartRow);
            if (srcRow >= startRow && srcRow <= endRow)
                return 1 + headerNRows + (srcRow - startRow);
            return -1;
        }

        // 填 cells
        foreach (var cell in allCells)
        {
            int lr = MapRow(cell.Row);
            if (lr < 0 || cell.Col < 0 || cell.Col >= maxNcols) continue;

            var tc = table.Cells[lr, cell.Col];
            if (!string.IsNullOrEmpty(cell.Text))
                tc.TextString = cell.Text;

            double th = cell.TextHeight > 0 ? cell.TextHeight : defaultTextHeight;
            tc.TextHeight = th;
            tc.Alignment = (CellAlignment)cell.Alignment;


            if (cell.Bold && boldTextStyleId != ObjectId.Null)
                tc.TextStyleId = boldTextStyleId;
        }

        // 合并（header 和 data 分别处理，不允许跨区域合并）
        // +1 offset for Title Row
        // Header merges
        if (headerNRows > 0)
        {
            foreach (var merge in allMerges)
            {
                if (merge.R1 < headerStartRow || merge.R2 > headerEndRow) continue;
                int lr1 = 1 + (merge.R1 - headerStartRow);
                int lr2 = 1 + (merge.R2 - headerStartRow);
                try
                {
                    int c1 = Math.Max(merge.C1, 0);
                    int c2 = Math.Min(merge.C2, maxNcols - 1);
                    if (lr1 > lr2 || c1 > c2) continue;
                    var range = CellRange.Create(table, lr1, c1, lr2, c2);
                    table.MergeCells(range);
                }
                catch { }
            }
        }
        // Data merges
        foreach (var merge in allMerges)
        {
            if (merge.R2 < startRow || merge.R1 > endRow) continue;
            int lr1 = (merge.R1 < startRow) ? 0 : merge.R1 - startRow;
            int lr2 = (merge.R2 > endRow) ? dataRows - 1 : merge.R2 - startRow;
            try
            {
                int c1 = Math.Max(merge.C1, 0);
                int c2 = Math.Min(merge.C2, maxNcols - 1);
                if (lr1 > lr2 || c1 > c2) continue;
                var range = CellRange.Create(table, 1 + headerNRows + lr1, c1, 1 + headerNRows + lr2, c2);
                table.MergeCells(range);
            }
            catch { }
        }

        // GenerateLayout 必须在 AppendEntity 之前
        table.GenerateLayout();

        // GenerateLayout 会重置合并子单元格的 TextHeight 为 -1
        // 重新填充所有 header 行的 cell 内容（+1 offset）
        if (headerNRows > 0)
        {
            foreach (var cell in allCells)
            {
                int lr = MapRow(cell.Row);
                if (lr < 0 || lr < 1 || lr > headerNRows || cell.Col < 0 || cell.Col >= maxNcols) continue;
                try
                {
                    var tc = table.Cells[lr, cell.Col];
                    if (!string.IsNullOrEmpty(cell.Text))
                        tc.TextString = cell.Text;
                    double th = cell.TextHeight > 0 ? cell.TextHeight : defaultTextHeight;
                    tc.TextHeight = th;
                    tc.Alignment = (CellAlignment)cell.Alignment;
                    if (cell.Bold && boldTextStyleId != ObjectId.Null)
                        tc.TextStyleId = boldTextStyleId;
                }
                catch { }
            }
        }

        // 隐藏 Title Row（row 0）的左、右、上边框
        for (int c = 0; c < maxNcols; c++)
        {
            try
            {
                var borders = table.Cells[0, c].Borders;
                borders.Top.IsVisible = false;
                borders.Left.IsVisible = false;
                borders.Right.IsVisible = false;
            }
            catch { }
        }

        // 预计算每行的 isTextRow（用于相邻行判断）
        var localIsText = new bool[totalRows];
        for (int r = 0; r < totalRows; r++)
        {
            int sr;
            if (r < headerNRows)
                sr = headerStartRow + r;
            else
                sr = startRow + (r - headerNRows);
            localIsText[r] = sr >= 0 && sr < rowIsText.Count && rowIsText[sr];
        }

        // 逐单元格控制边框（+1 offset for Title Row，跳过 row 0）
        for (int r = 0; r < totalRows; r++)
        {
            int localRow = 1 + r;  // +1 for Title Row
            // 反推 srcRow
            int srcRow;
            if (r < headerNRows)
                srcRow = headerStartRow + r;
            else
                srcRow = startRow + (r - headerNRows);

            bool isTextRow = localIsText[r];

            for (int c = 0; c < maxNcols; c++)
            {
                try
                {
                    if (isTextRow)
                    {
                        // 文本行：上下边框取决于相邻行是否为表格行
                        bool aboveIsTable = (r > 0 && !localIsText[r - 1]);
                        bool belowIsTable = (r + 1 < totalRows && !localIsText[r + 1]);
                        var borders = table.Cells[localRow, c].Borders;
                        borders.Top.IsVisible = aboveIsTable;
                        borders.Bottom.IsVisible = belowIsTable;
                        borders.Left.IsVisible = false;
                        borders.Right.IsVisible = false;
                    }
                    else
                    {
                        if (borderMap.TryGetValue((srcRow, c), out var brd) && brd.Length > 0)
                        {
                            var borders = table.Cells[localRow, c].Borders;
                            borders.Top.IsVisible = brd.Contains('T');
                            borders.Bottom.IsVisible = brd.Contains('B');
                            borders.Left.IsVisible = brd.Contains('L');
                            borders.Right.IsVisible = brd.Contains('R');
                        }
                        else
                        {
                            // 无边框数据的空单元格 → 隐藏全部边框
                            var tc = table.Cells[localRow, c];
                            if (string.IsNullOrEmpty(tc.TextString))
                            {
                                var borders = tc.Borders;
                                borders.Top.IsVisible = false;
                                borders.Bottom.IsVisible = false;
                                borders.Left.IsVisible = false;
                                borders.Right.IsVisible = false;
                            }
                        }

                        // 表头下方第一行：上边线始终可见（保证表头底框不丢失）
                        bool isRightBelowHeader = (headerNRows > 0 && r == headerNRows);
                        if (isRightBelowHeader)
                        {
                            table.Cells[localRow, c].Borders.Top.IsVisible = true;
                        }

                        // 最后一行且后续还有表格数据：底框始终可见（跨列切分不断线）
                        bool isLastChunkRow = (r == totalRows - 1);
                        if (isLastChunkRow
                            && srcRow + 1 < rowIsText.Count && !rowIsText[srcRow + 1])
                        {
                            table.Cells[localRow, c].Borders.Bottom.IsVisible = true;
                        }
                    }
                }
                catch { }
            }
        }

        table.SuppressRegenerateTable(false);

        // Diagnostic: dump header row AFTER all processing (only chunks with header)
        if (headerNRows > 0)
        {
            Log($"  POST chunk {startRow}-{endRow} hdr[{headerStartRow}-{headerEndRow}]:");
            for (int c = 0; c < Math.Min(maxNcols, 19); c++)
            {
                try
                {
                    string txt = table.Cells[1, c].TextString ?? "";
                    if (txt.Length > 20) txt = txt.Substring(0, 20);
                    double? th = table.Cells[1, c].TextHeight;
                    Log($"    POST[1,{c}] txt='{txt}' th={th ?? 0:F0}");
                }
                catch (Exception ex) { Log($"    POST[1,{c}] ERR: {ex.Message}"); }
            }
        }

        modelSpace.AppendEntity(table);
        tr.AddNewlyCreatedDBObject(table, true);
    }

    private static double GetCycledValue(List<double> list, int index)
    {
        if (list == null || list.Count == 0)
            return 0.0;
        return list[index % list.Count];
    }
}
