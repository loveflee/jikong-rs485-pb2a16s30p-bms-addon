/root/share/mosquitto
[硬化] 防禦 Consumer 處理過慢導致 Broker 記憶體溢出
max_queued_messages 1000
[硬化] QoS 0 的即時數據絕對不排隊，來不及收就直接丟棄
queue_qos0_messages false

```
nano hardening.conf
```
```
max_queued_messages 1000
queue_qos0_messages false
```

restart ha add-on mosquitto
