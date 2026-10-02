package com.shifu.uavmobile;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends Activity {
    private WebView wv;
    private boolean started = false;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
        startBackend();
        wv = new WebView(this);
        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        wv.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, String url) {
                if (url != null && !url.startsWith("http://127.0.0.1:8765")) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); } catch (Exception e) {}
                    return true;
                }
                return false;
            }
        });
        setContentView(wv);
        showBootPage();
        new Thread(() -> { waitReady(40); runOnUiThread(() -> wv.loadUrl("http://127.0.0.1:8765/")); }, "uav-boot").start();
    }

    private void showBootPage() {
        wv.loadData("<html><body style='background:#0A0B0D;color:#8b93a3;font-family:sans-serif;text-align:center;padding-top:45vh'>正在启动本机服务…</body></html>", "text/html; charset=utf-8", null);
    }

    private void startBackend() {
        final File files = getFilesDir();
        final String libDir = getApplicationInfo().nativeLibraryDir;
        Thread t = new Thread(new Runnable() {
            @Override public void run() {
                try {
                    Python py = Python.getInstance();
                    PyObject mod = py.getModule("phone_server");
                    mod.callAttr("main", files.getAbsolutePath(), libDir);
                } catch (Throwable e) {
                    android.util.Log.e("UAVMobile", "backend failed", e);
                }
            }
        }, "uav-backend");
        t.setDaemon(true);
        t.start();
    }

    /** 轮询本机端口直到后端就绪（最多 maxS 秒）。 */
    private void waitReady(int maxS) {
        for (int i = 0; i < maxS * 2; i++) {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:8765/api/status").openConnection();
                c.setConnectTimeout(1500); c.setReadTimeout(1500);
                if (c.getResponseCode() == 200) { c.disconnect(); return; }
                c.disconnect();
            } catch (Exception ignored) {}
            try { Thread.sleep(500); } catch (InterruptedException ignored) {}
        }
    }

    @Override public void onBackPressed() {
        if (wv != null && wv.canGoBack()) wv.goBack(); else super.onBackPressed();
    }
}