@echo off
set /p msg=Commit message: 
git add .
git commit -m "%msg%"
git pull --rebase
git push
pause