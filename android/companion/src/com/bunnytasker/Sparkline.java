package com.bunnytasker;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.util.AttributeSet;
import android.view.View;

/**
 * A balance trend line.
 *
 * The ledger already records `balance_after` on every row — charges always
 * did, and payments started to once the partial-payment credit was fixed — so
 * the shape of what the bunny owes over time was sitting in data the app
 * already fetched, rendered as a list of numbers nobody adds up.
 *
 * Deliberately dependency-free and tiny: no chart library, no axes, no
 * labels. It answers one question ("is this going up or down, and how fast")
 * at a glance, and the row list underneath answers everything else.
 *
 * Values are oldest-first. Fewer than two points draws nothing.
 */
public class Sparkline extends View {

    private float[] values = new float[0];
    private final Paint linePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint fillPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();

    public Sparkline(Context c) { this(c, null); }

    public Sparkline(Context c, AttributeSet a) {
        super(c, a);
        linePaint.setStyle(Paint.Style.STROKE);
        linePaint.setStrokeWidth(2.5f);
        linePaint.setStrokeJoin(Paint.Join.ROUND);
        linePaint.setStrokeCap(Paint.Cap.ROUND);
        fillPaint.setStyle(Paint.Style.FILL);
        dotPaint.setStyle(Paint.Style.FILL);
    }

    /** Oldest-first balances. Pass an empty array to blank the view. */
    public void setValues(float[] v) {
        values = v == null ? new float[0] : v;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        if (values.length < 2) return;

        float min = values[0], max = values[0];
        for (float v : values) {
            if (v < min) min = v;
            if (v > max) max = v;
        }
        // A flat line has no range to normalise against; centre it rather
        // than dividing by zero.
        float range = max - min;
        boolean flat = range < 0.0001f;

        float padV = 6f;
        float w = getWidth();
        float h = getHeight();
        float usable = h - padV * 2f;
        float stepX = w / (values.length - 1);

        // Rising = owing more = the Lion's colour. Falling = being paid off.
        boolean rising = values[values.length - 1] > values[0];
        int stroke = rising ? 0xFFcc7755 : 0xFF66aa66;
        linePaint.setColor(stroke);
        dotPaint.setColor(stroke);
        fillPaint.setColor((rising ? 0x22cc7755 : 0x2266aa66));

        path.reset();
        float lastX = 0f, lastY = 0f;
        for (int i = 0; i < values.length; i++) {
            float x = i * stepX;
            float norm = flat ? 0.5f : (values[i] - min) / range;
            float y = padV + (1f - norm) * usable;
            if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
            lastX = x;
            lastY = y;
        }

        // Fill under the line, then the line, then a dot on "where you are now".
        Path filled = new Path(path);
        filled.lineTo(lastX, h);
        filled.lineTo(0f, h);
        filled.close();
        canvas.drawPath(filled, fillPaint);
        canvas.drawPath(path, linePaint);
        canvas.drawCircle(lastX, lastY, 3.5f, dotPaint);
    }
}
