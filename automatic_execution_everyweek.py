# -*- coding: UTF-8 -*-
import MeCab
import re
import collections
import pymssql
import pandas as pd
from sklearn.linear_model import LinearRegression
import datetime
import time
import sys
import logging
import os
import configparser


IGNORE_WORDS = set([])  # 重要度計算外とする語

# ひらがな
JP_HIRA = set([chr(i) for i in range(12353, 12436)])
# カタカナ
JP_KATA = set([chr(i) for i in range(12449, 12532+1)])
#要忽略的字符
MULTIBYTE_MARK = set([
     "、", "。", "”", "“", "，", "《", "》", "：", "（", "）", "；",".","/","・","～"
    "〈", "〉", "「", "」", "『", "』", "【", "】", "〔", "〕", "？", "！",
    "ー", "-", "ー", "…", "‘", "’", "／","/"
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "０", "１", "２", "３",
    "４", "５", "６", "７", "８", "９",
    "①","②","③","④","⑤","⑥","⑦","⑧","⑨","⑩","⑪","⑫",
    "\r\n","\t","\n",
    "a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z",
    "A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z"
    ])


def cmp_noun_list(data):
    """
    和文テキストを受け取り、複合語（空白区切りの単名詞）のリストを返す
    """
    savetxt_list=[]
    deltxt_list = []
    # mecab = MeCab.Tagger("-Ochasen") #有词性标注的
    mecab = MeCab.Tagger("-Owakati")  # 没有词性标注的
    cmp_nouns = mecab.parse(data)
    cmp_nouns = cmp_nouns.split(" ")
    for every_keyword in cmp_nouns:
        if (len(every_keyword.strip())==1 and (every_keyword in MULTIBYTE_MARK or every_keyword in JP_HIRA or every_keyword in JP_KATA)):
            deltxt_list.append(every_keyword)
        else:
            savetxt_list.append(every_keyword)
    cmp_nouns = savetxt_list
    terms = []
    _increase(cmp_nouns, terms)
    return cmp_nouns


def _increase(cmp_nouns, terms):
    """
    専門用語リストへ、整形して追加するサブルーチン
    """
    if len(terms) > 1:
        cmp_noun = ' '.join(terms)
        cmp_nouns.append(cmp_noun)
    del terms[:]


def cmp_noun_dict(data):
    """
    複合語（単名詞の空白区切り）をキーに、その出現回数を値にしたディクショナリを返す
    """
    cmp_noun = cmp_noun_list(data)
    return list2dict(cmp_noun)


def list2dict(list_data):
    """
    リストの要素をキーに、その出現回数を値にしたディクショナリを返す
    """
    dict_data = {}
    for data in list_data:
        if data in dict_data:
            dict_data[data] += 1
        else:
            dict_data[data] = 1
    return dict_data


def score_lr(frequency, ignore_words=None, average_rate=1, lr_mode=1, dbm=None):
    """
    専門用語とそれを構成する単名詞の情報から重要度を計算する
        cmp_noun
            複合語（単名詞の空白区切り）をキーに出現回数を値に
            したディクショナリ
        ignore_word
            重要度計算の例外とする語のリスト
        average_rate
            重要度計算においてLRとFrequencyの比重を調整する
            数値が小さいほうがLRの比重が大きい
        lr_mode
            1のときはLRの計算において「延べ数」をとる
            2のときはLRの計算において「異なり数」をとる
    """
    # 対応する関数を呼び出し
    if dbm is None:
        noun_importance = _score_lr_dict(frequency, ignore_words, average_rate, lr_mode)
    else:
        noun_importance = _score_lr_dbm(frequency, ignore_words, average_rate, lr_mode, dbm)
    return noun_importance


def score_lr(frequency, ignore_words=None, average_rate=1, lr_mode=1, dbm=None):
    """
    専門用語とそれを構成する単名詞の情報から重要度を計算する
        cmp_noun
            複合語（単名詞の空白区切り）をキーに出現回数を値に
            したディクショナリ
        ignore_word
            重要度計算の例外とする語のリスト
        average_rate
            重要度計算においてLRとFrequencyの比重を調整する
            数値が小さいほうがLRの比重が大きい
        lr_mode
            1のときはLRの計算において「延べ数」をとる
            2のときはLRの計算において「異なり数」をとる
    """
    # 対応する関数を呼び出し
    if dbm is None:
        noun_importance = _score_lr_dict(frequency, ignore_words, average_rate, lr_mode)
    else:
        noun_importance = _score_lr_dbm(frequency, ignore_words, average_rate, lr_mode, dbm)
    return noun_importance


def _score_lr_dbm(frequency, ignore_words=None, average_rate=1, lr_mode=1, dbm=None):
    """
    dbmに蓄積したLR情報をもとにLRのスコアを出す
    """
    # 「専門用語」をキーに、値を「重要度」にしたディクショナリ
    noun_importance = {}
    stat = dbm    # 単名詞ごとの連接情報
    for cmp_noun in frequency.keys():
        importance = 1       # 専門用語全体の重要度
        count = 0     # 専門用語中の単名詞数をカウント
        if re.match(r"\s*$", cmp_noun):
            continue
        for noun in cmp_noun.split(" "):
            if re.match(r"[\d\.\,]+$", noun):
                continue
            left_score = 0
            right_score = 0
            if noun in stat:
                value = stat[noun].decode("utf-8").split("\t")
                if lr_mode == 1:  # 連接語の”延べ数”をとる場合
                    left_score = int(value[0])
                    right_score = int(value[1])
                elif lr_mode == 2:  # 連接語の”異なり数”をとる場合
                    left_score = int(value[3])
                    right_score = int(value[4])
            if noun not in ignore_words and not re.match(r"[\d\.\,]+$", noun):
                importance *= (left_score + 1) * (right_score + 1)
                count += 1
        if count == 0:
            count = 1
        # 相乗平均でLR重要度を出す
        importance = importance ** (1 / (2 * average_rate * count))
        noun_importance[cmp_noun] = importance
        count = 0
    return noun_importance


def _score_lr_dict(frequency, ignore_words, average_rate=1, lr_mode=1):
    # 「専門用語」をキーに、値を「重要度」にしたディクショナリ
    noun_importance = {}
    stat = {}  # 単名詞ごとの連接情報
    # 専門用語ごとにループ
    for cmp_noun in frequency.keys():
        if not cmp_noun:
            continue
        org_nouns = cmp_noun.split(" ")
        nouns = []
        # 数値及び指定の語を重要度計算から除外
        for noun in org_nouns:
            if ignore_words:
                if noun in ignore_words:
                    continue
            elif re.match(r"[\d\.\,]+$", noun):
                continue
            nouns.append(noun)
        # 複合語の場合、連接語の情報をディクショナリに入れる
        if len(nouns) > 1:
            for i in range(0, len(nouns)-1):
                if not nouns[i] in stat:
                    stat[nouns[i]] = [0, 0]
                if not nouns[i+1] in stat:
                    stat[nouns[i+1]] = [0, 0]
                if lr_mode == 1:  # 連接語の”延べ数”をとる場合
                    stat[nouns[i]][0] += frequency[cmp_noun]
                    stat[nouns[i+1]][1] += frequency[cmp_noun]
                elif lr_mode == 2:   # 連接語の”異なり数”をとる場合
                    stat[nouns[i]][0] += 1
                    stat[nouns[i+1]][1] += 1
    for cmp_noun in frequency.keys():
        importance = 1  # 専門用語全体の重要度
        count = 0  # ループカウンター（専門用語中の単名詞数をカウント）
        if re.match(r"\s*$", cmp_noun):
            continue
        for noun in cmp_noun.split(" "):
            if re.match(r"[\d\.\,]+$", noun):
                continue
            left_score = 0
            right_score = 0
            if noun in stat:
                left_score = stat[noun][0]
                right_score = stat[noun][1]
            importance *= (left_score + 1) * (right_score + 1)
            count += 1
        if count == 0:
            count = 1
        # 相乗平均でlr重要度を出す
        importance = importance ** (1 / (2 * average_rate * count))
        noun_importance[cmp_noun] = importance
        count = 0
    return noun_importance


def score_lr(frequency, ignore_words=None, average_rate=1, lr_mode=1, dbm=None):
    """
    専門用語とそれを構成する単名詞の情報から重要度を計算する
        cmp_noun
            複合語（単名詞の空白区切り）をキーに出現回数を値に
            したディクショナリ
        ignore_word
            重要度計算の例外とする語のリスト
        average_rate
            重要度計算においてLRとFrequencyの比重を調整する
            数値が小さいほうがLRの比重が大きい
        lr_mode
            1のときはLRの計算において「延べ数」をとる
            2のときはLRの計算において「異なり数」をとる
    """
    # 対応する関数を呼び出し
    if dbm is None:
        noun_importance = _score_lr_dict(frequency, ignore_words, average_rate, lr_mode)
    else:
        noun_importance = _score_lr_dbm(frequency, ignore_words, average_rate, lr_mode, dbm)
    return noun_importance


def term_importance(*args):
    """
    複数のディクショナリの値同士を乗算する
    """
    master = {}
    new_master = {}
    for noun_dict in args:
        for nouns, importance in noun_dict.items():
            if nouns in master:
                new_master[nouns] = master[nouns] * importance
            else:
                new_master[nouns] = importance
        master = new_master.copy()
    return master


def modify_agglutinative_lang(data):
    """
    半角スペースで区切られた単名詞を膠着言語（日本語等）向けに成形する
    """
    data_disp = ""
    eng = 0
    eng_pre = 0
    for noun in data.split(" "):
        if re.match("[A-Z|a-z]+$", noun):
            eng = 1
        else:
            eng = 0
        # 前後ともアルファベットなら半角空白空け、それ以外なら区切りなしで連結
        if eng and eng_pre:
            data_disp = data_disp + " " + noun
        else:
            data_disp = data_disp + noun
        eng_pre = eng
    return data_disp


def calculate_importance(president_txt,member_txt):
    tatext_president = president_txt
    tatext_member = member_txt
    # 複合語を抽出し、重要度を算出
    frequency_president = cmp_noun_dict(tatext_president)
    frequency_member = cmp_noun_dict(tatext_member)
    LR_president = score_lr(frequency_president, ignore_words=IGNORE_WORDS, lr_mode=1, average_rate=1)
    term_imp_president = term_importance(frequency_president, LR_president)
    LR_member = score_lr(frequency_member, ignore_words=IGNORE_WORDS, lr_mode=1, average_rate=1)
    term_imp_member = term_importance(frequency_member, LR_member)
    # 重要度が高い順に並べ替えて出力
    data_collection_president = collections.Counter(term_imp_president)
    data_collection_member = collections.Counter(term_imp_member)
    totalImportance_president, totalImportance_member = 0, 0
    key_words_lenth_president = len(data_collection_president)
    key_words_lenth_member = len(data_collection_member)
    key_words_list_president = []
    for cmp_noun, value in data_collection_president.most_common():
        totalImportance_president += value
        key_words_list_president.append(cmp_noun)
    key_words_list_memeber = []
    sum_matching_degree = 0
    matches_keywords_count = 0  # 社员参照教师报告后,与教师有多少个关键字匹配
    for cmp_noun, value in data_collection_member.most_common():
        totalImportance_member += value
        key_words_list_memeber.append(cmp_noun)
        if cmp_noun in key_words_list_president:
            matches_keywords_count += 1
            sum_matching_degree += value
    return key_words_lenth_president,key_words_lenth_member,totalImportance_president,totalImportance_member,sum_matching_degree,matches_keywords_count


def get_year_week_from_Mst_date(server, user, password, database, current_date):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param current_date:系统当前日期年-月-日
    :return:Mst_date表返回的当前年和当前周
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = " select year_no,week_no from Mst_date where date_mst='%s' "  % current_date
        cur.execute(sql)
        rows = cur.fetchall()
        if rows != []:
            current_year = rows[0][0]
            current_week = rows[0][1]
            return current_year,current_week
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method get_year_week_from_Mst_date() error!Can not query from table Mst_date!")
        logger.error("Exception:" + str(ex))
        raise ex
    finally:
        conn.close()


def read_dateConfig_file_set_year_week():
    global report_year
    global coef_week_list
    if os.path.exists(os.path.join(os.path.dirname(__file__), "dateConfig.ini")):
        try:
            conf = configparser.ConfigParser()
            conf.read(os.path.join(os.path.dirname(__file__), "dateConfig.ini"), encoding="utf-8-sig")
            year = conf.get("execute_year", "year")
            week = conf.get("execute_week", "week")
            report_year = year
            coef_week_list = [int(week)]
        except Exception as ex:
            logger.error("Content in dateConfig.ini has error.")
            logger.error("Exception:" + str(ex))
            raise ex


def read_report_from_database(server, user, password, database,report_week,employee_code,report_year = datetime.datetime.now().year):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param report_year:top报告年份
    :param report_week:top报告周
    :param employee_code:社员号
    :return:top报告内容
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = "select remark from report where report_year =%s and report_week =%s and employee_code =%s" \
              % (report_year, report_week, employee_code)
        cur.execute(sql)
        rows = cur.fetchall()
        if rows!=[]:
            content = rows[0][0].replace("<br>", "")
            return content
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method read_report_from_database() error!Can not query from table report!")
        logger.error("Exception:" + str(ex))
        raise ex
    finally:
        conn.close()


def calculate_employee_average_point(server, user, password, database,report_year,report_week,employee_code):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param report_year:top报告年份
    :param report_week:top报告周
    :param employee_code:社员号
    :return:平均分
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = "select count(1), convert(decimal(18, 2), sum(marking)/convert(decimal(18, 2), count(1))), cast(employee_code as int) from report_est where report_year = %s and report_week = %s and employee_code=%s and check_code != 90001038  group by employee_code " \
              % (report_year, report_week, employee_code)
        cur.execute(sql)
        rows = cur.fetchall()
        if rows != []:
            if rows[0][1] is not None:
                score = rows[0][1]
                return score
            else:#如果平均分为NULL
                return 0
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method calculate_employee_average_point() error!Can not query from table report_est!")
        logger.error("Exception:"+str(ex))
        raise ex
    finally:
        conn.close()


def generate_allweeks_employeelist_data(employee_teacher_list, report_week_list, report_year = datetime.datetime.now().year):
    '''
    :param employee_code_list:要插入到report_est_automatic表的社员号+教师号列表
    :param report_year:top报告年份
    :param report_week_list:top报告周列表
    :return:要插入到report_est_automatic表的list
    '''
    if len(employee_teacher_list)>0 and len(report_week_list)>0 and (report_year is not None) :
        report_year = report_year#年
        report_week_list = report_week_list#周列表
        employee_teacher_list = employee_teacher_list#社员号+教师号列表
        report_est_automatic_list = []
        for week in report_week_list:
            for employee_teacher in employee_teacher_list:
                teacher_report = read_report_from_database(server, user, password, database, week, employee_teacher[1], report_year)  # 教师TOP报告内容
                employee_report = read_report_from_database(server, user, password, database, week, employee_teacher[0], report_year)  # 社员TOP报告内容
                if teacher_report!='' and employee_report!='':
                    average_score = str(calculate_employee_average_point(server, user, password, database, report_year, week, employee_teacher[0]))  # 计算平均分marking
                    if average_score != '':
                        result = calculate_importance(teacher_report, employee_report)
                        importance_degree = str(result[3])
                        matching_degree = str(result[4])
                        add_list = [str(report_year),str(week),str(employee_teacher[0]),employee_teacher[1],importance_degree,matching_degree,average_score]
                        report_est_automatic_list.append(add_list)
        return report_est_automatic_list
    else:
        logger.error("Call method generate_allweeks_employeelist_data() error!"
                 +" Length of employee_teacher_list:"+str(len(employee_teacher_list))
                 +" Length of report_week_list:"+str(len(report_week_list))
                 +" Value of report_year:"+str(report_year))
        raise


def get_employee_list(server, user, password, database, report_year = datetime.datetime.now().year):
    '''
    :param server: 服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param report_year:top报告年份
    :return:只含有社员号的员工列表
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = " select distinct cast(employee_code as int) as employee_code "\
              " from report_est where report_year=%s and exists ( select 1  from report where report_est.employee_code=report.employee_code and report_year=%s ) "\
              " order by employee_code " \
              % (report_year,report_year)
        cur.execute(sql)
        rows = cur.fetchall()
        if rows != []:
            employee_list = []
            for employee in rows:
                employee_list.append(str(employee[0]))
            return employee_list
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method get_employee_list() error!Can not query from table report_target!")
        logger.error("Exception:"+str(ex))
        raise ex
    finally:
        conn.close()


def get_employee_list_from_table_report_target(server, user, password, database):
    '''
    :param server: 服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :return:report_target表去重后的社员号列表
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = "select distinct cast(employee_code as int) from report_target"
        cur.execute(sql)
        rows = cur.fetchall()
        if rows != []:
            employee_list = []
            for employee in rows:
                employee_list.append(str(employee[0]))
            return employee_list
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method get_employee_list_from_table_report_target() error!Can not query from table report_target!")
        logger.error("Exception:"+str(ex))
        raise ex
    finally:
        conn.close()


def get_employee_and_teacher_list_from_table_report_target(server, user, password, database):
    '''
       :param server: 服务器名称
       :param user:用户名
       :param password:密码
       :param database:数据库名
       :return:report_target表去重后的社员号+教师号列表
       '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = " select distinct cast(employee_code as int),cast(teacher_code as int) from report_target "
        cur.execute(sql)
        rows = cur.fetchall()
        employee_teacher_list = []
        if rows != []:
            for row in rows:
                every_row_list = []
                for i in range(len(row)):
                    every_row_list.append(row[i])
                employee_teacher_list.append(every_row_list)
            return employee_teacher_list
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method get_employee_and_teacher_list_from_table_report_target() error!Can not query from table report_target!")
        logger.error("Exception:" + str(ex))
        raise ex
    finally:
        conn.close()


def calculate_intercept_coef(server, user, password, database, coef_week_list, coef_employee_list, report_year = datetime.datetime.now().year):
    '''
    :param server: 服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param report_year:top报告年份
    :param coef_week_list:生成切片数据(切片(误差)、重要度、マッチ度)所使用的周列表
    :param coef_employee_list:生成切片数据(切片(误差)、重要度、マッチ度)所使用的社员号列表
    :return:切片(误差)、重要度、マッチ度
    '''
    if (report_year is not None) and len(coef_week_list)>0 and len(coef_employee_list)>0:
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            report_year = report_year
            coef_week_list = [str(i) for i in coef_week_list ]
            coef_week_list = ','.join(coef_week_list)
            coef_employee_list = ','.join(coef_employee_list)
            sql = " select cast(importance_degree as int),cast(matching_degree as int),making_Average from report_est_automatic" \
                  " where making_Average != 0 and report_year in (%s) and report_week in (%s) and employee_code in (%s) "\
                  %(report_year,coef_week_list,coef_employee_list)
            cur.execute(sql)
            rows = cur.fetchall()
            coef_list = []
            if rows != []:
                for row in rows:
                    coef_list.append(list(row))
            else:
                logger.error("coef_list is null. Can not query any row form table report_est_automatic.")
                raise
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method calculate_intercept_coef() error!Can not query from table report_est_automatic!" )
            logger.error("Exception:"+str(ex))
            raise ex
        finally:
            conn.close()
        if len(coef_list)>0:
            df = pd.DataFrame(coef_list)
            X = df.iloc[:, 0:2]
            y = df.iloc[:, [-1]]
            clf = LinearRegression()
            clf.fit(X, y)
            return clf.intercept_[0], clf.coef_[0][0], clf.coef_[0][1]#返回:切片(误差)、重要度、マッチ度
        else:
            return None,None,None#如果coef_list为空，返回3个None
    else:
        logger.error("Call method calculate_intercept_coef() error!"+"Length of coef_week_list:"+str(len(coef_week_list))+" Length of coef_employee_list:"+str(len(coef_employee_list)))
        raise


def insert_report_est_automatic(server, user, password, database, datalist):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param datalist:要插入数据的列表
    :return:无
    '''
    if len(datalist)>0:
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            for one_row in datalist:
                report_year = one_row[0]
                report_week = one_row[1]
                employee_code = one_row[2]
                check_code = one_row[3]
                importance_degree = one_row[4]
                matching_degree = one_row[5]
                marking = one_row[6]
                sql = ' insert into report_est_automatic (report_year, report_week, employee_code,check_code,importance_degree,matching_degree,making_Average) ' \
                      ' values(%s, %s, %s, %s, %s, %s, %s) '\
                      %(report_year,report_week,employee_code,check_code,importance_degree,matching_degree,marking)
                cur.execute(sql)
                conn.commit()
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method insert_report_est_automatic() error!")
            logger.error("Exception:"+str(ex))
            conn.rollback()
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method insert_report_est_automatic() error!Can not insert into table report_est_automatic! Length of datalist:"+str(len(datalist)))
        raise


def insert_report_coefficient(server, user, password, database,teacher_list,intercept,importance_degree,matching_degree,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param teacher_list:教师数据列表
    :param intercept:切片
    :param importance_degree:重要度
    :param matching_degree:マッチ度
    :param report_year:top报告年份
    :param report_week:top报告周
    :return:无
    '''
    if (intercept is not None) and (importance_degree is not None) and (matching_degree is not None):
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            sql = ' insert into report_coefficient (report_year, report_week, teacher_code,deviation,importance_degree,matching_degree ) ' \
                  ' values(%s, %s, %s, %s, %s, %s ) '\
                  %(report_year,report_week[0],teacher_list[0],intercept,importance_degree,matching_degree)
            cur.execute(sql)
            conn.commit()
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method insert_report_coefficient() error!")
            logger.error("Exception:"+str(ex))
            conn.rollback()
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method insert_report_coefficient() error!Can not insert into table report_coefficient! deviation: "+str(intercept)+" importance_degree:"+str(importance_degree)+" matching_degree:"+str(matching_degree))
        raise


def calculate_automatic_marking(server, user, password, database, intercept, importance_degree, matching_degree,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):#计算"自动采点"
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param intercept:切片
    :param importance_degree:重要度
    :param matching_degree:マッチ度
    :param report_year:top报告年份
    :param report_week:top报告周
    :return:更新report_est_automatic表的列automatic_marking(自动采点)需要用到的list
    '''
    if (intercept is not None) and (importance_degree is not None) and (matching_degree is not None):
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            sql = ' select report_year,report_week,cast(employee_code as int),cast(check_code as int),cast(importance_degree as int),cast(matching_degree as int),making_Average ' \
                  ' from report_est_automatic ' \
                  ' where report_year = %s and report_week = %s and employee_code in (select employee_code from report_target ) '\
                  % (report_year, report_week[0])
            cur.execute(sql)
            rows = cur.fetchall()
            report_est_automatic_list = []
            if rows != []:
                for row in rows:
                    automatic_marking = intercept + row[4]*importance_degree + row[5]*matching_degree
                    every_row_list = []
                    for i in range(len(row)):
                        every_row_list.append(row[i])
                    every_row_list.append(automatic_marking)
                    report_est_automatic_list.append(every_row_list)
                return report_est_automatic_list
            else:
                return ""
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method calculate_automatic_marking() error! Can not query from table report_est_automatic!")
            logger.error("Exception:"+str(ex))
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method calculate_automatic_marking() error!Can not query from report_est_automatic! deviation: " + str(intercept) + " importance_degree:" + str(importance_degree) + " matching_degree:" + str(matching_degree))
        raise


def update_col_automatic_marking(server, user, password, database,update_employee_list):#更新report_est_automatic表的automatic_marking
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param update_employee_list:更新report_est_automatic表的列automatic_marking(自动采点)需要用到的list
    :return:无
    '''
    need_to_update_list = update_employee_list
    if len(need_to_update_list)>0:
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            for row in need_to_update_list:
                report_year = row[0]
                report_week = row[1]
                employee_code = row[2]
                check_code = row[3]
                automatic_marking = row[7]
                sql = ' update report_est_automatic ' \
                      ' set automatic_marking= %s '\
                      ' where report_year=%s and report_week =%s and employee_code = %s and check_code=%s '\
                      %(automatic_marking,report_year,report_week,employee_code,check_code)
                cur.execute(sql)
                conn.commit()
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method update_col_automatic_marking() error! Can not update table report_est_automatic!")
            logger.error("Exception:"+str(ex))
            conn.rollback()
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method update_col_automatic_marking() error!Can not update table report_est_automatic! Length of update_employee_list: " + str(len(update_employee_list)))
        raise


def delete_current_data_from_report_est_automatic(server, user, password, database,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):
    '''
    :param server: 服务器名称
    :param user: 用户名
    :param password: 密码
    :param database: 数据库名
    :param report_year:top报告年份
    :param report_week:top报告周
    :return:无
    '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = ' delete from report_est_automatic where report_year = %s and report_week = %s' \
              ' and employee_code in (select distinct employee_code from report_target) ' \
              % (report_year, report_week[0])
        cur.execute(sql)
        conn.commit()
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method delete_current_data_from_report_est_automatic() error!")
        logger.error("Exception:" + str(ex))
        conn.rollback()
        raise ex
    finally:
        conn.close()


def delete_current_data_from_report_est(server, user, password, database,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):
    '''
      :param server: 服务器名称
      :param user: 用户名
      :param password: 密码
      :param database: 数据库名
      :param report_year:top报告年份
      :param report_week:top报告周
      :return:无
      '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        check_code = 90001038
        sql = ' delete from report_est where report_year = %s and report_week = %s' \
              ' and check_code = %s' \
              % (report_year, report_week[0], check_code)
        cur.execute(sql)
        conn.commit()
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method delete_current_data_from_report_est() error!")
        logger.error("Exception:" + str(ex))
        conn.rollback()
        raise ex
    finally:
        conn.close()


def delete_current_data_from_report_coefficient(server, user, password, database,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):
    '''
      :param server: 服务器名称
      :param user: 用户名
      :param password: 密码
      :param database: 数据库名
      :param report_year:top报告年份
      :param report_week:top报告周
      :return:无
      '''
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = ' delete from report_coefficient where report_year = %s and report_week = %s' \
              % (report_year, report_week[0])
        cur.execute(sql)
        conn.commit()
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method delete_current_data_from_report_coefficient() error!")
        logger.error("Exception:" + str(ex))
        conn.rollback()
        raise ex
    finally:
        conn.close()


def insert_auto_pick_point_to_report_est(server, user, password, database,insert_auto_pick_point_list):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param insert_auto_pick_point_list:插入到report_est表check_code = 90001038的自动采点的人员list
    :return:无
    '''
    need_to_insert_list = insert_auto_pick_point_list
    if len(need_to_insert_list)>0:
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            for row in need_to_insert_list:
                report_year = row[0]
                report_week = row[1]
                employee_code = row[2]
                check_code = 90001038
                automatic_marking = row[7]
                sql = ' insert into report_est (report_year, report_week, employee_code,check_code,marking ) ' \
                      ' values(%s, %s, %s, %s, %s ) ' \
                      % (report_year, report_week, employee_code, check_code, automatic_marking)
                cur.execute(sql)
                conn.commit()
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method insert_auto_pick_point_to_report_est() error! Can not insert into table report_est!")
            logger.error("Exception:"+str(ex))
            conn.rollback()
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method insert_auto_pick_point_to_report_est() error!Can not insert into table report_est! Length of insert_auto_pick_point_list: " + str(len(insert_auto_pick_point_list)))
        raise


def insert_not_exist_person_in_report_target_to_report_est(server, user, password, database,report_year = datetime.datetime.now().year,report_week = [datetime.datetime.now().isocalendar()[1]]):#当周没提交top报告的人需要向report_est表插入一条check_code为90001038,automatic_marking为0的数据
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param report_year:top报告年份
    :param report_week:top报告周
    :return:无
    '''
    employee_list = []
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = " select distinct cast(employee_code  as int) from report_target where employee_code not in " \
              "(select cast(employee_code as int) from report where report_year =%s and report_week=%s) "\
                %(report_year,report_week[0])
        cur.execute(sql)
        rows = cur.fetchall()
        if rows != []:
            for employee in rows:
                employee_list.append(str(employee[0]))
            for employee in employee_list:
                check_code = 90001038
                automatic_marking = 0
                sql = ' insert into report_est (report_year, report_week, employee_code,check_code,marking ) ' \
                      ' values(%s, %s, %s, %s, %s ) ' \
                      % (report_year, report_week[0], employee, check_code, automatic_marking)
                cur.execute(sql)
                conn.commit()
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method insert_not_exist_person_in_report_target_to_report_est() error! Can not insert into table report_est!")
        logger.error("Exception:"+str(ex))
        conn.rollback()
        raise ex
    finally:
        conn.close()


def output_keywords_importance(txt):
    frequency = cmp_noun_dict(txt)
    LR = score_lr(frequency,ignore_words=IGNORE_WORDS,lr_mode=1, average_rate=1)
    term_imp = term_importance(frequency, LR)
    data_collection = collections.Counter(term_imp)
    for cmp_noun, value in data_collection.most_common():
        print(modify_agglutinative_lang(cmp_noun) ,value, sep="\t")


def write_log():
    '''
    :return: 返回logger对象
    '''
    # 获取logger实例，如果参数为空则返回root logger
    logger = logging.getLogger()
    now_date = datetime.datetime.now().strftime('%Y%m%d')
    log_file = now_date+".log"# 文件日志
    if not os.path.exists("log"):#python文件同级别创建log文件夹
        os.makedirs("log")
    # 指定logger输出格式
    formatter = logging.Formatter('%(asctime)s %(levelname)s line:%(lineno)s %(message)s')
    file_handler = logging.FileHandler("log" + os.sep + log_file, mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter) # 可以通过setFormatter指定输出格式
    # 为logger添加的日志处理器，可以自定义日志处理器让其输出到其他地方
    logger.addHandler(file_handler)
    # 指定日志的最低输出级别，默认为WARN级别
    logger.setLevel(logging.INFO)
    return logger


if __name__=="__main__":
    logger = write_log()  # 获取日志对象
    time_start = datetime.datetime.now()
    start = time.clock()
    logger.info("Program start,now time is:"+str(time_start))
    # 数据库服务器信息
    server = "172.17.254.154"
    user = "test"
    password = "test"
    database = "TRIAL"
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")#系统当前日期
    current_year,current_week = get_year_week_from_Mst_date(server, user, password, database, current_date)#从Mst_date获取当前年和周
    report_year = str(current_year)#当前系统年
    coef_week_list = [current_week] #当前系统周
    teacher_list = ["1"]  # 插入到report_coefficient表需要用的教师号
    read_dateConfig_file_set_year_week()#读配置文件设置report_year和coef_week_list
    # employee_list = get_employee_list(server, user, password, database, report_year)#获取全员的员工列表(返回社员号列表)
    coef_employee_list = get_employee_list_from_table_report_target(server, user, password, database)#从report_target表获取员工列表(返回社员号列表)
    coef_employee_and_teacher_list = get_employee_and_teacher_list_from_table_report_target(server, user, password, database)#从report_target表获取员工号和教师号列表(返回社员号和教师号列表)
    delete_current_data_from_report_est_automatic(server, user, password, database, report_year, coef_week_list)#从report_est_automatic表删除当前周数据
    data_addto_report_est_automatic = generate_allweeks_employeelist_data(coef_employee_and_teacher_list, coef_week_list,report_year)#生成X年、X周、社员号X、教师数据的社员号X、重要度X、匹配度X、平均分X的列表
    insert_report_est_automatic(server, user, password, database, data_addto_report_est_automatic)#插入到report_est_automatic
    delete_current_data_from_report_coefficient(server, user, password, database, report_year, coef_week_list)#从report_coefficient表删除当前周数据
    intercept_coef = calculate_intercept_coef(server, user, password, database, coef_week_list, coef_employee_list, report_year)#获取切片、重要度、マッチ度
    insert_report_coefficient(server, user, password, database,teacher_list,intercept_coef[0],intercept_coef[1],intercept_coef[2],report_year,coef_week_list)#插入report_coefficient表数据
    automatic_marking_list = calculate_automatic_marking(server, user, password, database,intercept_coef[0],intercept_coef[1],intercept_coef[2],report_year,coef_week_list)#计算自动采点
    # update_col_automatic_marking(server, user, password, database,automatic_marking_list)#更新report_est_automatic表的automatic_marking(自动采点)
    delete_current_data_from_report_est(server, user, password, database, report_year, coef_week_list)  # 从report_est表删除当前周数据
    insert_auto_pick_point_to_report_est(server, user, password, database,automatic_marking_list)#插入自动采点的90001038到report_est表
    insert_not_exist_person_in_report_target_to_report_est(server, user, password, database,report_year,coef_week_list)#将report_target表有而report表没有的人(就是当周没提交top报告的人),插入到report_est(check_code:90001038;marking:0)
    time_end = datetime.datetime.now()
    end = time.clock()
    logger.info("Program end,now time is:"+str(time_end))
    logger.info("Program run : %f seconds" % (end - start))



