#!/usr/bin/env bash
# Starts a genuine HDFS NameNode and DataNode on this machine, bound to loopback.
# Data lives in .local/hdfs (git-ignored). Requires: brew install openjdk@17 hadoop
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export JAVA_HOME="${JAVA_HOME:-$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home}"
export HADOOP_LOG_DIR="$ROOT/.local/hadoop-logs"
export HADOOP_PID_DIR="$ROOT/.local/hadoop-pids"
export HADOOP_HEAPSIZE_MAX=1g
NN_DIR="$ROOT/.local/hdfs/name"
DN_DIR="$ROOT/.local/hdfs/data"
# Runtime config: the tracked templates plus this checkout's storage paths and Hadoop's logging defaults.
CONF="$ROOT/.local/hadoop-conf"
mkdir -p "$CONF" "$HADOOP_LOG_DIR" "$HADOOP_PID_DIR"
cp "$(brew --prefix hadoop)"/libexec/etc/hadoop/log4j*.properties "$CONF"/ 2>/dev/null || true
cp configs/hadoop/core-site.xml "$CONF/core-site.xml"
sed "s|</configuration>|  <property><name>dfs.namenode.name.dir</name><value>file://$NN_DIR</value></property>\n  <property><name>dfs.datanode.data.dir</name><value>file://$DN_DIR</value></property>\n</configuration>|" \
  configs/hadoop/hdfs-site.xml > "$CONF/hdfs-site.xml"
export HADOOP_CONF_DIR="$CONF"

case "${1:-}" in
  start)
    if [ ! -d "$NN_DIR/current" ]; then
      hdfs namenode -format -nonInteractive -force >/dev/null 2>&1
      echo "formatted NameNode at $NN_DIR"
    fi
    hdfs --daemon start namenode || true
    hdfs --daemon start datanode || true
    hdfs dfsadmin -safemode wait
    hdfs dfsadmin -report | grep -E "Live datanodes|Configured Capacity" | head -2
    ;;
  stop) hdfs --daemon stop datanode || true; hdfs --daemon stop namenode || true ;;
  status) jps 2>/dev/null | grep -E "NameNode|DataNode" || echo "not running" ;;
  env) echo "export JAVA_HOME=$JAVA_HOME HADOOP_CONF_DIR=$HADOOP_CONF_DIR" ;;
  *) echo "usage: $0 {start|stop|status|env}"; exit 2 ;;
esac
