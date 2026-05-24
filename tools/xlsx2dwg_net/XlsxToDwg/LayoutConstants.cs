namespace XlsxToDwg;

public static class Layout
{
    // A1 图框 (1:100)
    public const double FRAME_W = 84100;
    public const double FRAME_H = 59400;
    public const double FRAME_GAP = 2000;

    // 绘图区边界
    public const double DRAW_LEFT = 2500;
    public const double DRAW_BOTTOM = 1000;
    public const double DRAW_RIGHT = 77100;
    public const double DRAW_TOP = 58400;
    public const double DRAW_W = DRAW_RIGHT - DRAW_LEFT;   // 74600
    public const double DRAW_H = DRAW_TOP - DRAW_BOTTOM;   // 57400

    // 标题区
    public const double TITLE_AREA_H = 5000;
    public const double CONTENT_TOP = DRAW_TOP - TITLE_AREA_H;  // 53400
    public const double CONTENT_H = CONTENT_TOP - DRAW_BOTTOM;  // 52400

    // 3 列布局
    public const int COL_COUNT = 3;
    public const double COL_GAP = 1500;
    public const double COL_LEFT_PAD = -500;    // 第一列右移 500
    public const double COL_RIGHT_PAD = -500;   // 第三列左移 500
    public const double COL_W = (DRAW_W + COL_LEFT_PAD + COL_RIGHT_PAD - COL_GAP * (COL_COUNT - 1)) / COL_COUNT;  // ≈23567

    // 列内容最大高度
    public const double MAX_COL_H = CONTENT_H - 2000;  // 50400

    // xlsx 列宽缩放因子
    public const double XLSX_SCALE = 2100;

    // 字高
    public const double H1 = 320;      // 一级标题：一、工程概况
    public const double H2 = 310;      // 二级标题：（一）设计依据
    public const double H_BODY = 300;  // 正文
    public const double H_TITLE = 600; // 页标题

    // 内容起始 Y 偏移
    public const double CONTENT_START_OFFSET = 2000; // 从 CONTENT_TOP 往下 2000 开始
    public const double TITLE_Y_OFFSET = 1000;       // 标题距 DRAW_TOP 1000

    // 中文数字
    public static readonly string[] CN_NUMS =
    {
        "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
        "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"
    };
}
