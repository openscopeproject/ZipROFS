#!/bin/bash

TOTALTESTS=0
PASSEDTESTS=0
PASTELOG=$1

function runtest {
    ((TOTALTESTS+=1))
    TESTDESCR="$1"
    EXPECTED="$2"
    TESTCOMMAND="$3"
    echo "--------------------------"
    echo "Running test: $TESTDESCR"
    RESULT=$(bash -c "$TESTCOMMAND")
    if [[ "$EXPECTED" != "$RESULT" ]]; then
        echo "FAIL"
        echo -e "Expected:\n$EXPECTED"
        echo -e "Got:\n$RESULT"
        echo "Diff:"
        diff <(echo "$EXPECTED") <(echo "$RESULT")
        return 1
    else
        echo "PASS"
        ((PASSEDTESTS+=1))
        return 0
    fi
}

echo "Running ziprofs tests..."
REPODIR="$(dirname $(dirname $(readlink -f "$0")))"
echo "Mounting filesystem"
if [ ! -d "$REPODIR/test/mnt" ]; then
  mkdir -p "$REPODIR/test/mnt"
fi
if [ ! -z "$(mount | grep "$REPODIR/test/mnt")" ]; then
    fusermount -u "$REPODIR/test/mnt"
fi
"$REPODIR/ziprofs.py" "$REPODIR/test/data" "$REPODIR/test/mnt" -o foreground,debug > "$REPODIR/test/test.log" 2>&1 &
PID=$!
sleep 1
cd "$REPODIR/test/mnt"

runtest "zip is directory" "./test.zip" 'find ./ -type d -name test.zip'

TREERESULT=$(tree -a --noreport ../data | tail -n +2 | grep -v test.zip)
runtest "tree" "$TREERESULT" 'tree -a --noreport ./test.zip | tail -n +2'

runtest "reading file content #1" "$(cat ../data/text.txt)" 'cat test.zip/text.txt'
runtest "reading file content #2" "$(cat ../data/folder/subfolder/file.txt)" 'cat test.zip/folder/subfolder/file.txt'

runtest "running script" "hello" 'test.zip/script.sh'

cd ..
echo "Killing ziprofs"
fusermount -u "$REPODIR/test/mnt"
kill $PID

echo "$PASSEDTESTS/$TOTALTESTS tests passed."
if [[ "$PASTELOG" == "-log" ]]; then
  echo "Copying test log in full:"
  echo
  cat "$REPODIR/test/test.log"
fi

if [[ $PASSEDTESTS != $TOTALTESTS ]]; then
  exit 1
fi
