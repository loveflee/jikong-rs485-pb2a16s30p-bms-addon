/root/share/mosquitto
```
nano mosquitto.conf 
```
```
max_queued_messages 1000
queue_qos0_messages false
#max_inflight_messages 20
#max_packet_size 0
```

restart ha add-on mosquitto
