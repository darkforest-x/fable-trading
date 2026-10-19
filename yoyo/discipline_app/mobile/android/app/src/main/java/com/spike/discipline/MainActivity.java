package com.spike.discipline;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DeviceSessionPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
