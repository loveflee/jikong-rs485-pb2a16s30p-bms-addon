# 建立 udev 規則文件 (針對 CH340/CH341 系列，VID 為 1a86)
tee /etc/udev/rules.d/99-jkbms.rules > /dev/null << 'EOF'
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", RUN+="/usr/bin/docker restart jkbms"
EOF

# 重新載入 udev 規則
udevadm control --reload-rules
udevadm trigger
