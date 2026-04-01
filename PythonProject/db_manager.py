- name: 💾 保存最新數據 (Git Push)
      run: |
        git config --local user.email "action@github.com"
        git config --local user.name "GitHub Action Bot"
        
        # 🔥 加入 -f 強制追蹤資料庫，突破 .gitignore 封鎖！
        git add -f car_listings_v2.db
        
        git diff --quiet && git diff --staged --quiet || (git commit -m "📊 自動更新資料庫" && git push)
