package com.bunnytasker;

import android.graphics.Bitmap;

/**
 * Minimal pure-Java QR code encoder. No external dependencies.
 * Supports byte mode, error correction level L, versions 1-10.
 * Based on ISO/IEC 18004 with simplified implementation.
 */
public class QrEncoder {

    public static Bitmap encode(String data, int moduleSize) {
        try {
            byte[] dataBytes = data.getBytes("UTF-8");
            int version = selectVersion(dataBytes.length);
            if (version < 0) return null;
            int size = 17 + version * 4;
            boolean[][] grid = new boolean[size][size];
            boolean[][] reserved = new boolean[size][size];

            // Place finder patterns
            placeFinder(grid, reserved, 0, 0);
            placeFinder(grid, reserved, size - 7, 0);
            placeFinder(grid, reserved, 0, size - 7);

            // Timing patterns
            for (int i = 8; i < size - 8; i++) {
                grid[6][i] = (i % 2) == 0;
                grid[i][6] = (i % 2) == 0;
                reserved[6][i] = true;
                reserved[i][6] = true;
            }

            // Alignment pattern (version 2+)
            if (version >= 2) {
                int[] aligns = getAlignmentPositions(version);
                for (int ay : aligns) {
                    for (int ax : aligns) {
                        if (isFinderArea(ax, ay, size)) continue;
                        placeAlignment(grid, reserved, ax, ay);
                    }
                }
            }

            // Dark module
            grid[size - 8][8] = true;
            reserved[size - 8][8] = true;

            // Reserve format info areas
            for (int i = 0; i < 9; i++) {
                if (i < size) { reserved[8][i] = true; reserved[i][8] = true; }
            }
            for (int i = 0; i < 8; i++) {
                reserved[8][size - 1 - i] = true;
                reserved[size - 1 - i][8] = true;
            }

            // Reserve version info (version 7+)
            if (version >= 7) {
                for (int i = 0; i < 6; i++) {
                    for (int j = 0; j < 3; j++) {
                        reserved[i][size - 11 + j] = true;
                        reserved[size - 11 + j][i] = true;
                    }
                }
            }

            // Encode data
            byte[] encoded = encodeData(dataBytes, version);

            // Place data bits
            placeData(grid, reserved, encoded, size);

            // Apply mask 0 (checkerboard) and format info
            applyMaskAndFormat(grid, reserved, size, version);

            // Render to bitmap
            int quiet = 4;
            int bmpSize = (size + quiet * 2) * moduleSize;
            Bitmap bmp = Bitmap.createBitmap(bmpSize, bmpSize, Bitmap.Config.ARGB_8888);
            for (int y = 0; y < bmpSize; y++) {
                for (int x = 0; x < bmpSize; x++) {
                    int my = y / moduleSize - quiet;
                    int mx = x / moduleSize - quiet;
                    boolean dark = my >= 0 && my < size && mx >= 0 && mx < size && grid[my][mx];
                    bmp.setPixel(x, y, dark ? 0xFF000000 : 0xFFFFFFFF);
                }
            }
            return bmp;
        } catch (Exception e) {
            return null;
        }
    }

    private static int selectVersion(int dataLen) {
        // Byte mode capacities for ECC level L
        int[] caps = {0, 17, 32, 53, 78, 106, 134, 154, 192, 230, 271};
        for (int v = 1; v < caps.length; v++) {
            if (dataLen <= caps[v]) return v;
        }
        return -1;
    }

    private static void placeFinder(boolean[][] g, boolean[][] r, int row, int col) {
        for (int dy = -1; dy <= 7; dy++) {
            for (int dx = -1; dx <= 7; dx++) {
                int y = row + dy, x = col + dx;
                if (y < 0 || y >= g.length || x < 0 || x >= g.length) continue;
                boolean dark;
                if (dy == -1 || dy == 7 || dx == -1 || dx == 7) dark = false;
                else if (dy == 0 || dy == 6 || dx == 0 || dx == 6) dark = true;
                else if (dy >= 2 && dy <= 4 && dx >= 2 && dx <= 4) dark = true;
                else dark = false;
                g[y][x] = dark;
                r[y][x] = true;
            }
        }
    }

    private static void placeAlignment(boolean[][] g, boolean[][] r, int cx, int cy) {
        for (int dy = -2; dy <= 2; dy++) {
            for (int dx = -2; dx <= 2; dx++) {
                int y = cy + dy, x = cx + dx;
                boolean dark = Math.abs(dy) == 2 || Math.abs(dx) == 2 || (dy == 0 && dx == 0);
                g[y][x] = dark;
                r[y][x] = true;
            }
        }
    }

    private static boolean isFinderArea(int x, int y, int size) {
        return (x <= 8 && y <= 8) || (x <= 8 && y >= size - 8) || (x >= size - 8 && y <= 8);
    }

    private static int[] getAlignmentPositions(int version) {
        // Simplified alignment positions for versions 2-10
        int[][] table = {
            {}, {6, 18}, {6, 22}, {6, 26}, {6, 30}, {6, 34},
            {6, 22, 38}, {6, 24, 42}, {6, 26, 46}, {6, 28, 50}, {6, 30, 54}
        };
        return version < table.length ? table[version] : new int[]{};
    }

    private static byte[] encodeData(byte[] data, int version) {
        // Total data codewords for version at ECC level L
        int[] totalCodewords = {0, 19, 34, 55, 80, 108, 136, 156, 194, 232, 274};
        int[] eccCodewords = {0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18};
        int totalCw = totalCodewords[version];
        int eccCw = eccCodewords[version];
        int dataCw = totalCw - eccCw;

        // Build data stream: mode indicator (0100 = byte) + char count + data + terminator
        java.io.ByteArrayOutputStream bits = new java.io.ByteArrayOutputStream();
        int charCountBits = version <= 9 ? 8 : 16;

        // We'll work in bytes, packing bits manually
        byte[] stream = new byte[dataCw];
        int bitPos = 0;

        // Mode indicator: 0100 (byte mode)
        bitPos = writeBits(stream, bitPos, 0x4, 4);
        // Character count
        bitPos = writeBits(stream, bitPos, data.length, charCountBits);
        // Data
        for (byte b : data) {
            bitPos = writeBits(stream, bitPos, b & 0xFF, 8);
        }
        // Terminator
        bitPos = writeBits(stream, bitPos, 0, Math.min(4, dataCw * 8 - bitPos));
        // Pad to byte boundary
        if (bitPos % 8 != 0) bitPos = writeBits(stream, bitPos, 0, 8 - (bitPos % 8));
        // Pad codewords
        int pos = bitPos / 8;
        boolean alt = true;
        while (pos < dataCw) {
            stream[pos++] = (byte) (alt ? 0xEC : 0x11);
            alt = !alt;
        }

        // Generate ECC (Reed-Solomon)
        byte[] ecc = generateECC(stream, dataCw, eccCw);

        // Combine
        byte[] result = new byte[totalCw];
        System.arraycopy(stream, 0, result, 0, dataCw);
        System.arraycopy(ecc, 0, result, dataCw, eccCw);
        return result;
    }

    private static int writeBits(byte[] buf, int bitPos, int value, int numBits) {
        for (int i = numBits - 1; i >= 0; i--) {
            if (bitPos / 8 >= buf.length) return bitPos;
            if (((value >> i) & 1) == 1) {
                buf[bitPos / 8] |= (byte) (0x80 >> (bitPos % 8));
            }
            bitPos++;
        }
        return bitPos;
    }

    private static byte[] generateECC(byte[] data, int dataCw, int eccCw) {
        // Reed-Solomon over GF(256) with primitive polynomial 0x11D
        int[] gen = rsGeneratorPoly(eccCw);
        int[] msg = new int[dataCw + eccCw];
        for (int i = 0; i < dataCw; i++) msg[i] = data[i] & 0xFF;

        for (int i = 0; i < dataCw; i++) {
            int coef = msg[i];
            if (coef != 0) {
                for (int j = 0; j < gen.length; j++) {
                    msg[i + j] ^= gfMul(gen[j], coef);
                }
            }
        }
        byte[] ecc = new byte[eccCw];
        for (int i = 0; i < eccCw; i++) ecc[i] = (byte) msg[dataCw + i];
        return ecc;
    }

    private static int[] rsGeneratorPoly(int degree) {
        int[] poly = new int[degree + 1];
        poly[0] = 1;
        for (int i = 0; i < degree; i++) {
            int[] temp = new int[degree + 1];
            for (int j = 0; j <= i; j++) {
                temp[j] ^= gfMul(poly[j], 1);
                temp[j + 1] ^= gfMul(poly[j], gfPow(2, i));
            }
            System.arraycopy(temp, 0, poly, 0, degree + 1);
        }
        return poly;
    }

    private static final int[] GF_EXP = new int[512];
    private static final int[] GF_LOG = new int[256];
    static {
        int x = 1;
        for (int i = 0; i < 255; i++) {
            GF_EXP[i] = x;
            GF_LOG[x] = i;
            x <<= 1;
            if (x >= 256) x ^= 0x11D;
        }
        for (int i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
    }

    private static int gfMul(int a, int b) {
        if (a == 0 || b == 0) return 0;
        return GF_EXP[GF_LOG[a] + GF_LOG[b]];
    }

    private static int gfPow(int base, int exp) {
        int result = 1;
        for (int i = 0; i < exp; i++) result = gfMul(result, base);
        return result;
    }

    private static void placeData(boolean[][] grid, boolean[][] reserved, byte[] data, int size) {
        int bitIdx = 0;
        int totalBits = data.length * 8;
        boolean upward = true;

        for (int col = size - 1; col >= 0; col -= 2) {
            if (col == 6) col = 5; // Skip timing column
            for (int i = 0; i < size; i++) {
                int row = upward ? size - 1 - i : i;
                for (int dc = 0; dc <= 1; dc++) {
                    int c = col - dc;
                    if (c < 0 || c >= size) continue;
                    if (reserved[row][c]) continue;
                    if (bitIdx < totalBits) {
                        grid[row][c] = ((data[bitIdx / 8] >> (7 - bitIdx % 8)) & 1) == 1;
                        bitIdx++;
                    }
                }
            }
            upward = !upward;
        }
    }

    private static void applyMaskAndFormat(boolean[][] grid, boolean[][] reserved, int size, int version) {
        // Mask 0: (row + col) % 2 == 0
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                if (!reserved[y][x] && (y + x) % 2 == 0) {
                    grid[y][x] = !grid[y][x];
                }
            }
        }

        // Format info for ECC level L (01), mask 0 (000) = 01000
        // After BCH: 0x77C0... simplified: use precomputed format bits
        int formatBits = 0x77C4; // L, mask 0, with BCH ECC
        // XOR with mask pattern 0x5412
        formatBits ^= 0x5412;

        // Place format bits around finders
        for (int i = 0; i < 15; i++) {
            boolean bit = ((formatBits >> (14 - i)) & 1) == 1;
            // Around top-left finder
            if (i < 6) grid[8][i] = bit;
            else if (i == 6) grid[8][7] = bit;
            else if (i == 7) grid[8][8] = bit;
            else if (i == 8) grid[7][8] = bit;
            else grid[14 - i][8] = bit;

            // Around other finders
            if (i < 8) grid[size - 1 - i][8] = bit;
            else grid[8][size - 15 + i] = bit;
        }
    }
}
