package ca.guillevin.m580tv;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;

/** Fullscreen, read-only machine screen for the TV: tv.html in a WebView, fed by a Modbus poll thread. */
public class MainActivity extends Activity {
    private static final long POLL_MS = 500;
    private SharedPreferences prefs;
    private WebView web;
    private volatile boolean running;
    private volatile String latest = "{\"ok\":false,\"error\":\"Connecting…\"}";
    private volatile PlcClient client;

    public class Bridge {
        @JavascriptInterface public String snapshot() { return latest; }
        @JavascriptInterface public String host() { return prefs.getString("host", "192.168.10.10"); }
        @JavascriptInterface public int port() { return prefs.getInt("port", 502); }
        @JavascriptInterface public int unit() { return prefs.getInt("unit", 255); }
        @JavascriptInterface public void save(String host, int port, int unit) {
            prefs.edit().putString("host", host.trim()).putInt("port", port).putInt("unit", unit).apply();
            PlcClient old = client;
            if (old != null) old.close();
            client = null;
        }
    }

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("plc", MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setUseWideViewPort(true);                  // tv.html lays out at 1920 wide, whatever the panel
        s.setLoadWithOverviewMode(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.addJavascriptInterface(new Bridge(), "Plc");
        web.setBackgroundColor(0xFF111315);
        setContentView(web);
        web.loadUrl("file:///android_asset/tv.html");
    }

    @Override protected void onResume() {
        super.onResume();
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        running = true;
        new Thread(new Runnable() {
            @Override public void run() { pollLoop(); }
        }, "plc-poll").start();
    }

    @Override protected void onPause() {
        super.onPause();
        running = false;
    }

    private void pollLoop() {
        while (running) {
            long start = System.currentTimeMillis();
            PlcClient c = client;
            if (c == null) {
                c = new PlcClient(prefs.getString("host", "192.168.10.10"), prefs.getInt("port", 502), prefs.getInt("unit", 255));
                client = c;
            }
            try {
                latest = c.poll();
            } catch (Exception e) {
                String msg = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
                latest = "{\"ok\":false,\"error\":\"" + msg.replace("\\", " ").replace("\"", " ").replace("\n", " ") + "\"}";
            }
            try {
                Thread.sleep(Math.max(50, POLL_MS - (System.currentTimeMillis() - start)));
            } catch (InterruptedException e) {
                return;
            }
        }
        PlcClient c = client;
        if (c != null) c.close();
        client = null;
    }

    /** MENU or SETTINGS on the remote opens the PLC address form. */
    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_MENU || code == KeyEvent.KEYCODE_SETTINGS) {
            web.evaluateJavascript("toggleSetup()", null);
            return true;
        }
        return super.onKeyDown(code, e);
    }
}
