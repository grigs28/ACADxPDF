using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace XlsxToDwg;

/// <summary>
/// Excel 转换为 DWG 的 JSON 文档模型
/// </summary>
public class XlsxDocument
{
    [JsonPropertyName("version")]
    public int Version { get; set; }

    [JsonPropertyName("save_format")]
    public string SaveFormat { get; set; } = "2007";

    [JsonPropertyName("font")]
    public FontInfo? Font { get; set; }

    [JsonPropertyName("default_text_height")]
    public double DefaultTextHeight { get; set; } = 350.0;

    [JsonPropertyName("sections")]
    public List<SectionInfo> Sections { get; set; } = new();

    [JsonPropertyName("num_pages")]
    public int NumPages { get; set; } = 1;
}

/// <summary>
/// 字体信息
/// </summary>
public class FontInfo
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("charset")]
    public byte Charset { get; set; } = 134;

    [JsonPropertyName("pitch_and_family")]
    public byte PitchAndFamily { get; set; } = 2;
}

/// <summary>
/// 文档段落（MText 或 Table）
/// </summary>
public class SectionInfo
{
    [JsonPropertyName("type")]
    public string Type { get; set; } = "";

    // --- MText 字段 ---
    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("insert")]
    public List<double> Insert { get; set; } = new();

    [JsonPropertyName("width")]
    public double Width { get; set; }

    [JsonPropertyName("text_height")]
    public double TextHeight { get; set; }

    [JsonPropertyName("bold")]
    public bool Bold { get; set; }

    [JsonPropertyName("attachment")]
    public string? Attachment { get; set; }

    // --- Table 字段 ---
    [JsonPropertyName("nrows")]
    public int NRows { get; set; }

    [JsonPropertyName("ncols")]
    public int NCols { get; set; }

    [JsonPropertyName("col_widths")]
    public List<double> ColWidths { get; set; } = new();

    [JsonPropertyName("row_heights")]
    public List<double> RowHeights { get; set; } = new();

    [JsonPropertyName("default_text_height")]
    public double DefaultTextHeight { get; set; }

    [JsonPropertyName("borderless")]
    public bool Borderless { get; set; }

    [JsonPropertyName("title_suppressed")]
    public bool TitleSuppressed { get; set; } = true;

    [JsonPropertyName("vert_margin")]
    public double VertMargin { get; set; }

    [JsonPropertyName("horz_margin")]
    public double HorzMargin { get; set; }

    [JsonPropertyName("cells")]
    public List<CellInfo> Cells { get; set; } = new();

    [JsonPropertyName("merges")]
    public List<MergeInfo> Merges { get; set; } = new();

    [JsonPropertyName("row_borderless")]
    public List<bool> RowBorderless { get; set; } = new();
}

/// <summary>
/// 单元格信息
/// </summary>
public class CellInfo
{
    [JsonPropertyName("row")]
    public int Row { get; set; }

    [JsonPropertyName("col")]
    public int Col { get; set; }

    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("text_height")]
    public double TextHeight { get; set; }

    [JsonPropertyName("alignment")]
    public int Alignment { get; set; } = 5;

    [JsonPropertyName("bold")]
    public bool Bold { get; set; }
}

/// <summary>
/// 合并单元格信息
/// </summary>
public class MergeInfo
{
    [JsonPropertyName("r1")]
    public int R1 { get; set; }

    [JsonPropertyName("c1")]
    public int C1 { get; set; }

    [JsonPropertyName("r2")]
    public int R2 { get; set; }

    [JsonPropertyName("c2")]
    public int C2 { get; set; }
}
