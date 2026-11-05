package ca.guillevin.m580tv;

import android.app.Activity;
import android.content.Context;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/** The dashboard, fullscreen on the TV: its own page and server in the app, talking straight to the M580. */
public class MainActivity extends Activity {
    private static final int PORT = 8780;
    private static final String SETTINGS = "settings.json";
    private static Engine engine;                       // one for the app, whatever the Activity does
    private static WebServer server;
    private WebView web;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        final Context app = getApplicationContext();
        if (engine == null) {
            engine = new Engine(new Engine.Store() {
                @Override public String load() {
                    try (InputStream f = app.openFileInput(SETTINGS)) {
                        ByteArrayOutputStream b = new ByteArrayOutputStream();
                        byte[] buf = new byte[4096];
                        for (int n; (n = f.read(buf)) > 0; ) b.write(buf, 0, n);
                        return new String(b.toByteArray(), StandardCharsets.UTF_8);
                    } catch (IOException e) {
                        return null;
                    }
                }

                @Override public void save(String json) {
                    try (OutputStream f = app.openFileOutput(SETTINGS, MODE_PRIVATE)) {
                        f.write(json.getBytes(StandardCharsets.UTF_8));
                    } catch (IOException ignored) {
                    }
                }
            });
            try {
                server = new WebServer(engine, new WebServer.Files() {
                    @Override public InputStream open(String name) throws IOException {
                        return app.getAssets().open("web/" + name);
                    }
                }, PORT);
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportZoom(false);
        web.setBackgroundColor(0xFFF2F3F1);
        web.setWebViewClient(new WebViewClient());      // stays in the app
        web.setWebChromeClient(new WebChromeClient());  // confirm() dialogs
        setContentView(web);
        web.loadUrl("http://127.0.0.1:" + PORT + "/");
    }

    @Override protected void onResume() {
        super.onResume();
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        web.onResume();
        engine.setPaused(false);
    }

    @Override protected void onPause() {
        engine.setPaused(true);                         // off screen: the run drops and the PLC watchdog takes over
        web.onPause();
        super.onPause();
    }

    @Override protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }

    @Override public void onBackPressed() {
        // the back key stays in the app: leave with HOME
    }
}
