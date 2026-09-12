import json

d = json.load(open('playbook_truth.json', encoding='utf-8'))
today = "2026-09-13"
confirm_ids = ["W1","W2","W3","W6","W7","L2","L7","L9","L11","L13","L14","W13"]
for r in d['rules']:
    if r['id'] in confirm_ids:
        r['last_validated'] = today

d['changelog'].append({
    "version": 35,
    "date": today,
    "change": "9/13実測breakout(200件・中央値104・P90226・TOP1441)で再検証。W1(短断定「頭痛改善の第一歩は、自分のタイプを知ることです。」756v/310v)/W3(数字自己診断「月に何回、頭痛で薬に頼っていますか？」752v・「肩こり、1週間に何回感じますか？」493v)/W13(呼びかけ単体止め「頭痛のたびに薬…をやめたい人へ。」405vで再確認、通算6回目)がTOP15内に該当例あり再実証。L2(自己回収「その理由わかりますか？原因を変えないから」4v)/L7(第三者伝聞「そんなお声が増えています」3v・「そんな声をよく聞きます」1v。14件目の確認)/L9(実績クロージング「施術実績1万人の整体院です」6v)/L11(漠然共感問い「仕事の集中力も落ちませんか」2v)/L13(汎用自己紹介混入「長岡市で整体院を兄妹で運営しています」0v×2)/L14(呼びかけ+来院ロジ複合「長岡駅から車で5分・専用駐車場あり〜長岡市の整体院です」0v)はWORST20で再実証。W11(素の【】リスト型)/W12(画像投稿)は今回サンプルで新規該当なし・testing継続。lab_feedback未applied0件(全済)。prune実行。全社軌道遅れ(pace28%)のためヒーロー10本+新テンプレ24本(フック全面刷新)を実施。"
})
d['version'] = 35
d['updated'] = today
json.dump(d, open('playbook_truth.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print("done", d['version'])
