#!/bin/bash

TOTALTESTS=0
PASSEDTESTS=0

function runtest {
    ((TOTALTESTS+=1))
    TESTDESCR="$1"
    EXPECTED="$2"
    TESTCOMMAND="$3"
    echo "--------------------------"
    echo "Running test: $TESTDESCR"
    RESULT=$($TESTCOMMAND)
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
if [ ! -z "$(mount | grep "$REPODIR/test/mnt")" ]; then
    fusermount -u "$REPODIR/test/mnt"
fi
"$REPODIR/ziprofs.py" "$REPODIR/test/data" "$REPODIR/test/mnt" > "$REPODIR/test/test.log" 2>&1 &
PID=$!
sleep 1
cd "$REPODIR/test/mnt"

runtest "zip is directory" "./test.zip" 'find ./ -type d -name test.zip'

TREERESULT=$(tree -a ../data | tail -n +2 | grep -v test.zip)
#runtest "tree" "$TREERESULT" 'tree -a ./test.zip | tail -n +2'

cd ..
echo "Killing ziprofs"
kill $PID

echo "$PASSEDTESTS/$TOTALTESTS tests passed."
exit $([[ $PASSEDTESTS != $TOTALTESTS ]])
