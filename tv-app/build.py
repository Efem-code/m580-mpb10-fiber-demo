"""Builds M580-Machine-Health.apk with the Android command-line tools (no Android Studio or Gradle).

  python build.py <folder holding sdk\\ and jdk-17*\\>

Signs with keystore.jks here, made on the first build. Keep it: a later APK only installs over this
one if it's signed with the same key.
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = sys.argv[1]
JDK = glob.glob(os.path.join(TOOLS, "jdk-17*"))[0]
SDK = os.path.join(TOOLS, "sdk")
BT = os.path.join(SDK, "build-tools", "34.0.0")
JAR = os.path.join(SDK, "platforms", "android-30", "android.jar")
OUT = os.path.join(HERE, "build")
APK = os.path.join(HERE, "M580-Machine-Health.apk")
KEY, PASS = os.path.join(HERE, "keystore.jks"), "m580-demo-tv"
ENV = dict(os.environ, JAVA_HOME=JDK, PATH=os.path.join(JDK, "bin") + os.pathsep + os.environ["PATH"])


def run(*a):
    a = ["cmd", "/c", *a] if a[0].endswith(".bat") else list(a)
    subprocess.run(a, check=True, env=ENV, cwd=HERE)


shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(os.path.join(OUT, "classes"))
os.makedirs(os.path.join(OUT, "gen"))
res, unsigned, aligned = (os.path.join(OUT, n) for n in ("res.zip", "unsigned.apk", "aligned.apk"))

# the page is the dashboard's own, copied in at each build
shutil.rmtree(os.path.join(HERE, "assets"), ignore_errors=True)
shutil.copytree(os.path.join(HERE, "..", "dashboard", "static"), os.path.join(HERE, "assets", "web"))

run(os.path.join(BT, "aapt2.exe"), "compile", "--dir", "res", "-o", res)
run(os.path.join(BT, "aapt2.exe"), "link", "-I", JAR, "--manifest", "AndroidManifest.xml", "-A", "assets",
    "--java", os.path.join(OUT, "gen"), "-o", unsigned, res)
sources = glob.glob(os.path.join(HERE, "src", "**", "*.java"), recursive=True) + \
    glob.glob(os.path.join(OUT, "gen", "**", "*.java"), recursive=True)
run(os.path.join(JDK, "bin", "javac.exe"), "-encoding", "UTF-8", "-source", "8", "-target", "8", "-nowarn", "-Xlint:-options",
    "-bootclasspath", JAR, "-d", os.path.join(OUT, "classes"), *sources)
classes = glob.glob(os.path.join(OUT, "classes", "**", "*.class"), recursive=True)
run(os.path.join(BT, "d8.bat"), "--lib", JAR, "--min-api", "26", "--release", "--output", OUT, *classes)
with zipfile.ZipFile(unsigned, "a") as z:
    z.write(os.path.join(OUT, "classes.dex"), "classes.dex")
run(os.path.join(BT, "zipalign.exe"), "-f", "-p", "4", unsigned, aligned)
if not os.path.exists(KEY):
    run(os.path.join(JDK, "bin", "keytool.exe"), "-genkeypair", "-keystore", KEY, "-storepass", PASS, "-keypass", PASS,
        "-alias", "m580tv", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=M580 demo TV, O=Guillevin")
run(os.path.join(BT, "apksigner.bat"), "sign", "--ks", KEY, "--ks-pass", "pass:" + PASS, "--key-pass", "pass:" + PASS,
    "--out", APK, aligned)
run(os.path.join(BT, "apksigner.bat"), "verify", "--verbose", APK)
print(APK, os.path.getsize(APK), "bytes")
