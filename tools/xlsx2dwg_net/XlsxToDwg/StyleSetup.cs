using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.GraphicsInterface;

namespace XlsxToDwg;

/// <summary>
/// 文字样式和表格样式的创建与确保存在
/// </summary>
public static class StyleSetup
{
    public const string TextStyleName = "XlsxSongTi";
    public const string TableStyleName = "XlsxTableStyle";

    /// <summary>
    /// 确保文字样式 "XlsxSongTi" 存在，不存在则创建。
    /// 字体设置为传入的 FontInfo 指定的宋体等字体。
    /// </summary>
    /// <param name="tr">当前事务</param>
    /// <param name="db">数据库</param>
    /// <param name="font">字体信息（null 时使用默认宋体）</param>
    /// <returns>文字样式的 ObjectId</returns>
    public static ObjectId EnsureTextStyle(Transaction tr, Database db, FontInfo? font)
    {
        var textStyleTable = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForRead);

        if (textStyleTable.Has(TextStyleName))
        {
            return textStyleTable[TextStyleName];
        }

        // 升级为写模式
        textStyleTable.UpgradeOpen();

        var record = new TextStyleTableRecord();
        record.Name = TextStyleName;

        // 设置字体
        var fi = font ?? new FontInfo();
        var fontDesc = new FontDescriptor(fi.Name, false, false, fi.Charset, fi.PitchAndFamily);
        record.Font = fontDesc;

        var styleId = textStyleTable.Add(record);
        tr.AddNewlyCreatedDBObject(record, true);

        return styleId;
    }

    /// <summary>
    /// 确保表格样式 "XlsxTableStyle" 存在，不存在则创建。
    /// 对所有行类型（Title/Header/Data）设置文字高度和文字样式。
    /// Data 行和 Header 行的对齐方式设为 MiddleCenter。
    /// </summary>
    /// <param name="tr">当前事务</param>
    /// <param name="db">数据库</param>
    /// <param name="textStyleId">文字样式 ObjectId</param>
    /// <param name="defaultTextHeight">默认文字高度</param>
    /// <returns>表格样式的 ObjectId</returns>
    public static ObjectId EnsureTableStyle(Transaction tr, Database db, ObjectId textStyleId, double defaultTextHeight)
    {
        var dict = (DBDictionary)tr.GetObject(db.TableStyleDictionaryId, OpenMode.ForRead);

        if (dict.Contains(TableStyleName))
        {
            return dict.GetAt(TableStyleName);
        }

        // 升级为写模式
        dict.UpgradeOpen();

        var tableStyle = new TableStyle();
        var styleId = dict.SetAt(TableStyleName, tableStyle);
        tr.AddNewlyCreatedDBObject(tableStyle, true);

        // RowType 整数值: DataRow=1, TitleRow=2, HeaderRow=4
        // TableStyle API: SetTextHeight(double height, int rowTypes)
        // SetTextStyle(ObjectId styleId, int rowTypes)
        // SetAlignment(CellAlignment align, int rowTypes)
        // rowTypes 是位掩码，可以组合

        int allRows = 1 | 2 | 4;  // DataRow | TitleRow | HeaderRow
        int dataAndHeader = 1 | 4; // DataRow | HeaderRow

        // 对所有行类型设置文字高度和文字样式
        tableStyle.SetTextHeight(defaultTextHeight, allRows);
        tableStyle.SetTextStyle(textStyleId, allRows);

        // Data 行和 Header 行对齐方式设为 MiddleCenter
        tableStyle.SetAlignment(CellAlignment.MiddleCenter, dataAndHeader);

        return styleId;
    }
}
