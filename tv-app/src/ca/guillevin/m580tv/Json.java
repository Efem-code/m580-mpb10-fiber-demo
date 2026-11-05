package ca.guillevin.m580tv;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small JSON reader and writer: maps, lists, strings, numbers, booleans and null. */
final class Json {
    private final String s;
    private int i;

    private Json(String s) {
        this.s = s;
    }

    static Object parse(String text) {
        Json j = new Json(text);
        Object v = j.value();
        j.space();
        if (j.i != text.length()) throw new IllegalArgumentException("trailing JSON");
        return v;
    }

    static String write(Object v) {
        StringBuilder b = new StringBuilder();
        put(b, v);
        return b.toString();
    }

    private static void put(StringBuilder b, Object v) {
        if (v == null) {
            b.append("null");
        } else if (v instanceof String) {
            string(b, (String) v);
        } else if (v instanceof Map) {
            b.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) b.append(',');
                first = false;
                string(b, String.valueOf(e.getKey()));
                b.append(':');
                put(b, e.getValue());
            }
            b.append('}');
        } else if (v instanceof Iterable) {
            b.append('[');
            boolean first = true;
            for (Object o : (Iterable<?>) v) {
                if (!first) b.append(',');
                first = false;
                put(b, o);
            }
            b.append(']');
        } else {
            b.append(v);                         // numbers and booleans
        }
    }

    private static void string(StringBuilder b, String t) {
        b.append('"');
        for (int k = 0; k < t.length(); k++) {
            char c = t.charAt(k);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        b.append('"');
    }

    private void space() {
        while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++;
    }

    private Object value() {
        space();
        char c = s.charAt(i);
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return text();
        if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
        if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
        if (s.startsWith("null", i)) { i += 4; return null; }
        int start = i;
        while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
        return Double.valueOf(s.substring(start, i));
    }

    private Map<String, Object> object() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        i++;
        space();
        if (s.charAt(i) == '}') { i++; return m; }
        while (true) {
            space();
            String k = text();
            space();
            if (s.charAt(i++) != ':') throw new IllegalArgumentException("bad JSON");
            m.put(k, value());
            space();
            char c = s.charAt(i++);
            if (c == '}') return m;
            if (c != ',') throw new IllegalArgumentException("bad JSON");
        }
    }

    private List<Object> array() {
        List<Object> l = new ArrayList<Object>();
        i++;
        space();
        if (s.charAt(i) == ']') { i++; return l; }
        while (true) {
            l.add(value());
            space();
            char c = s.charAt(i++);
            if (c == ']') return l;
            if (c != ',') throw new IllegalArgumentException("bad JSON");
        }
    }

    private String text() {
        if (s.charAt(i++) != '"') throw new IllegalArgumentException("bad JSON");
        StringBuilder b = new StringBuilder();
        while (true) {
            char c = s.charAt(i++);
            if (c == '"') return b.toString();
            if (c != '\\') { b.append(c); continue; }
            char e = s.charAt(i++);
            switch (e) {
                case 'n': b.append('\n'); break;
                case 'r': b.append('\r'); break;
                case 't': b.append('\t'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                default: b.append(e);
            }
        }
    }
}
