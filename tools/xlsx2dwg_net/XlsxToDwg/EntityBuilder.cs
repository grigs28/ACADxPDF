using System;
using System.Collections.Generic;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace XlsxToDwg;

public static class EntityBuilder
{
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

    private static double GetCycledValue(List<double> list, int index)
    {
        if (list == null || list.Count == 0)
            return 0.0;
        return list[index % list.Count];
    }
}
