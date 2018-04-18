# -*- coding: UTF-8 -*-
import MeCab
import re
import collections
import pymssql
import datetime
import time
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
        if (every_keyword is not None and len(every_keyword.strip())==1 and (every_keyword in MULTIBYTE_MARK or every_keyword in JP_HIRA or every_keyword in JP_KATA)):
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



def calculate_importance(member_txt):
    tatext_member = member_txt
    content = re.sub('\s', '', member_txt)#去掉空白字符
    length_member_topReport_content = len(content)
    # 複合語を抽出し、重要度を算出
    frequency_member = cmp_noun_dict(tatext_member)
    LR_member = score_lr(frequency_member, ignore_words=IGNORE_WORDS, lr_mode=1, average_rate=1)
    term_imp_member = term_importance(frequency_member, LR_member)
    # 重要度が高い順に並べ替えて出力
    data_collection_member = collections.Counter(term_imp_member)
    totalImportance_member = 0
    # key_words_lenth_member = len(data_collection_member)
    key_words_list_memeber = []
    for cmp_noun, value in data_collection_member.most_common():
        totalImportance_member += value
        key_words_list_memeber.append(cmp_noun)
    return totalImportance_member,length_member_topReport_content


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


def read_dateConfig_file_set_database():
    if os.path.exists(os.path.join(os.path.dirname(__file__), "dateConfig.ini")):
        try:
            conf = configparser.ConfigParser()
            conf.read(os.path.join(os.path.dirname(__file__), "dateConfig.ini"), encoding="utf-8-sig")
            server = conf.get("server", "server")
            user = conf.get("user", "user")
            password = conf.get("password", "password")
            database = conf.get("database", "database")
            return server,user,password,database
        except Exception as ex:
            logger.error("Content in dateConfig.ini about database has error.")
            logger.error("Exception:" + str(ex))
            raise ex



def read_dateConfig_file_set_year_week():
    global report_year
    global report_week
    if os.path.exists(os.path.join(os.path.dirname(__file__), "dateConfig.ini")):
        try:
            conf = configparser.ConfigParser()
            conf.read(os.path.join(os.path.dirname(__file__), "dateConfig.ini"), encoding="utf-8-sig")
            year = conf.get("execute_year", "year")
            week = conf.get("execute_week", "week")
            if  year:
                report_year = year
            if week:
                report_week = week
        except Exception as ex:
            logger.error("Content in dateConfig.ini about execute_year or execute_week has error.")
            logger.error("Exception:" + str(ex))
            raise ex


def get_report_employee_list(server, user, password, database, report_year,report_week):
    try:
        conn = pymssql.connect(server, user, password, database)
        cur = conn.cursor()
        sql = " select report_year,report_week,cast(employee_code as int) from report " \
              " where report_year = %s and report_week = %s  " \
             % (report_year,report_week)
        cur.execute(sql)
        rows = cur.fetchall()
        employee_list = []
        if rows:
            for row in rows:
                employee_list.append(list(row))
            return employee_list
        else:
            return ""
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error(
            "Call method get_report_employee_list() error!Can not query from table report!")
        logger.error("Exception:" + str(ex))
        raise ex
    finally:
        conn.close()




def read_report_from_database(server, user, password, database,report_year,report_week,employee_code):
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



def generate_appointed_importance_data(server, user, password, database, employee_list, report_year, report_week):
    '''
    :param employee_list:report表再X年X周的社员列表
    :param report_year:top报告年份
    :param report_week_list:top报告周
    :return:要插入到新表的list,格式为年、周、社员号、自己文章的重要度、字数
    '''
    try:
        if employee_list:
            report_importance_list = []
            for employee in employee_list:
                employee_report = read_report_from_database(server, user, password, database, report_year,report_week,employee[2])# 社员TOP报告内容
                result = calculate_importance(employee_report)
                importance_degree = str(result[0])
                length_member_topReport = str(result[1])
                add_list = [str(report_year), str(report_week), str(employee[2]), importance_degree, length_member_topReport]
                report_importance_list.append(add_list)
            return report_importance_list
    except pymssql.Error as ex:
        logger.error("dbException:" + str(ex))
        raise ex
    except Exception as ex:
        logger.error("Call method generate_appointed_importance_data() error!There is a null value in the parameters.!")
        logger.error("Exception:" + str(ex))
        raise ex

def insert_report_importance(server, user, password, database, datalist):
    '''
    :param server:服务器名称
    :param user:用户名
    :param password:密码
    :param database:数据库名
    :param data_addto_report_importance:插入到report_importance的列表
    '''
    if datalist:
        try:
            conn = pymssql.connect(server, user, password, database)
            cur = conn.cursor()
            for one_row in datalist:
                report_year = one_row[0]
                report_week = one_row[1]
                employee_code = one_row[2]
                importance_degree = one_row[3]
                counter = one_row[4]
                sql = ' if not exists (select 1 from report_importance where report_year = %s and report_week = %s and employee_code = %s ) ' \
                      ' insert into report_importance (report_year, report_week, employee_code,importance_degree,counter) ' \
                      ' values(%s, %s, %s, %s, %s) '\
                      %(report_year,report_week,employee_code,report_year,report_week,employee_code,importance_degree,counter)
                cur.execute(sql)
                conn.commit()
        except pymssql.Error as ex:
            logger.error("dbException:" + str(ex))
            raise ex
        except Exception as ex:
            logger.error("Call method insert_report_importance() error!")
            logger.error("Exception:"+str(ex))
            conn.rollback()
            raise ex
        finally:
            conn.close()
    else:
        logger.error("Call method insert_report_est_automatic() error!There is a null value in the parameters.")
        raise






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
    # logger.info("Program start,now time is:"+str(time_start))
    server,user,password,database = read_dateConfig_file_set_database()#读取配置文件中的数据库信息
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")#系统当前日期
    current_year,current_week = get_year_week_from_Mst_date(server, user, password, database, current_date)#从Mst_date获取当前年和周
    report_year = str(current_year)#当前系统年
    report_week = str(current_week) #当前系统周
    read_dateConfig_file_set_year_week()#读配置文件设置report_year和report_week
    report_employee_list= get_report_employee_list(server, user, password, database,report_year,report_week)#从report表获取X年,X周,社员列表
    data_addto_report_importance = generate_appointed_importance_data(server, user, password, database, report_employee_list, report_year, report_week)#生成X年、X周、社员号X、重要度X、字数X的列表
    insert_report_importance(server, user, password, database, data_addto_report_importance)# 插入X年、X月、社员号X、重要度、top报告长度到report_importance
    time_end = datetime.datetime.now()
    end = time.clock()
    logger.info("Program end,now time is:"+str(time_end))
    logger.info("Program run : %f seconds" % (end - start))



