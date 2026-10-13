#!/usr/bin/perl
use Encode ();
use Fcntl qw(:flock O_RDWR O_CREAT O_EXCL);
use Time::Local ();
use FindBin qw($Bin);
chdir($Bin);   # 相対パスで動くので、カレントをスクリプトのあるディレクトリに固定する

$copyright = <<EOL;
<!--
     Script for amezo-like BBS
     Copyright (C) 1999-2003 amezo_
     http://www17.big.or.jp/~amezo_/
     http://ame.x0.com/
-->
EOL

$amezo = '＠あめぞう';
$urlbase = 'http://ame.x0.com/';
$admin = 'サポート';

# 同じ板への連続投稿の最短間隔(秒)。
$post_interval = 5;

# 板に残すスレッド数の上限。0 なら無制限。
$max_threads = 0;

# 「次のページ」の再生成を使い回す秒数。0 なら使い回さない。
$page_cache = 10;

# 接続元IP信頼設定
@trusted_proxies = ('127.0.0.1', '::1');

# スレッド削除機能の合言葉ハッシュファイル
$delcryptfile = 'delpass.txt';

$ENV{'SCRIPT_FILENAME'} =~ /\/([^\/]+)$/;
$cgi = $1;
$cgi = 'amezo.cgi' unless $cgi;
$cgi =~ s/\r?\n//g; # 改行を完全に除去

$content_len = $ENV{'CONTENT_LENGTH'} || 0;
($content_len =~ /^\d+$/ && $content_len <= 7500) || &error('投稿内容が大きすぎます');
read(STDIN, $buffer, $content_len);
length($buffer) > 7500 && &error('投稿内容が大きすぎます');

# GETパラメータのパース
%query_params = ();
if ($ENV{'QUERY_STRING'}) {
    foreach $pair (split(/&/, $ENV{'QUERY_STRING'})) {
        my ($name, $value) = split(/=/, $pair, 2);
        $value =~ tr/+/ /;
        $value =~ s/%([a-fA-F0-9][a-fA-F0-9])/pack("C", hex($1))/eg;
        $query_params{$name} = $value;
    }
}

# POSTパラメータのパース
@pairs = split(/&/,$buffer);
foreach $pair (@pairs) {
    ($name, $value) = split(/=/, $pair, 2);
    $value =~ tr/+/ /;
    $value =~ s/%([a-fA-F0-9][a-fA-F0-9])/pack("C", hex($1))/eg;
    $value = &to_utf8($value);
    $value = &strip_invisible($value);
    $value =~ s/&/&amp;/g;
    $value =~ s/"/&quot;/g;
    $value =~ s/</&lt;/g;
    $value =~ s/>/&gt;/g;
    $value =~ s/'/&#39;/g;
    $value =~ s/\r\n/\r/g;
    $value =~ s/\n/\r/g;
    $value =~ s/\r/<br>/g if $name eq 'comm';
    $value =~ s/[\x00-\x1f]//g;
    $form{$name} = $value;
}

# 板名（フォルダ）決定
$folder = '';
if (length($form{'folder'})) {
    $folder = $form{'folder'};
} elsif (length($query_params{'folder'})) {
    $folder = $query_params{'folder'};
} else {
    $referer = $ENV{'HTTP_REFERER'} || '';
    $referer =~ s/%([a-fA-F0-9][a-fA-F0-9])/pack("C", hex($1))/eg;
    $urlbase_cmp = $urlbase;
    $urlbase_cmp =~ s{^(https?://)www\.}{$1};
    $referer =~ s{^(https?://)www\.}{$1};
    if ($referer =~ m{https?://[^/]+/([^/]+)/}) {
        $folder = $1;
    }
}

&error('板名が判定できません。') if $folder eq '';
&error('板名が不正です') if $folder =~ m{/|\.\.} || !&is_board($folder);

# HTTP_ORIGIN と HTTP_REFERER の安全検証の最適化（Originが無い場合はRefererでチェック）
$origin = $ENV{'HTTP_ORIGIN'} || '';
$referer = $ENV{'HTTP_REFERER'} || '';
$http_host = $ENV{'HTTP_HOST'} || '';

if ($origin ne '') {
    $urlbase_cmp = $urlbase;
    $urlbase_cmp =~ s{^(https?://)www\.}{$1};
    $origin_cmp = $origin;
    $origin_cmp =~ s{^(https?://)www\.}{$1};

    # オリジンが設定値と異なる場合、ホスト名を用いた検証を行う
    if (index($urlbase_cmp, "$origin_cmp/") != 0) {
        if ($http_host ne '') {
            my $clean_host = $http_host;
            $clean_host =~ s/\:\d+$//; # ポート番号の除去
            if (index($origin_cmp, $clean_host) == -1) {
                &error('不正なリクエストです(Origin不一致)');
            }
        } else {
            # Origin判定不可を回避するために、Originがホスト名と直接不一致ではない限りはRefererチェックへ流す
            if ($referer ne '') {
                my $clean_host = $http_host;
                $clean_host =~ s/\:\d+$//;
                if (index($referer, $clean_host) == -1 && index($referer, $urlbase) == -1) {
                    &error('不正なリクエストです(Origin判定不可/Referer不一致)');
                }
            }
        }
    }
} elsif ($referer ne '') {
    # Originヘッダが無い場合はRefererのホスト名チェックでフォールバック
    if ($http_host ne '') {
        my $clean_host = $http_host;
        $clean_host =~ s/\:\d+$//;
        if (index($referer, $clean_host) == -1 && index($referer, $urlbase) == -1) {
            &error('不正なリクエストです(Referer不一致)');
        }
    }
} else {
    # OriginもRefererも一切取得できない環境の場合は安全のため警告するか、ローカル環境は緩和する
    # Colab + Cloudflare トンネルの特性を考慮し、ここではエラーを厳しくしすぎず通します
}

$name = $form{'name'};
$mail = $form{'mail'};
$comm = $form{'comm'};
$subj = $form{'subj'};

$res = $form{'res'};
$res = '' unless defined $res;
$res =~ /^\d*$/ || &error('不正なスレッド指定です');
length($form{'sub'}) && &res;
$next = $form{'next'};
$next = 1 unless defined $next && $next =~ /^\d{1,6}$/ && $next > 0;
$next += 20;
$reload = 1 if $name eq $subj && $name eq $comm;
$reload = 1 unless length($name) || length($subj) || length($comm);
@a = ($comm =~ /<br>/g);
@a + 0 > 20 && &error("改行が多すぎます");
&clen($subj) > 20 && &error("題名が長すぎます");
$name =~ s/ |　|\xE2\x80\x8D//g;
$name = &ccut($name, 30);
$comm =~ s/&gt;(&gt;)+/&gt;/g;
$subj =~ s/ |　|＿//g;

$comm2 = $comm;
$comm2 =~ s/%([a-fA-F0-9][a-fA-F0-9])/pack("C", hex($1))/eg;
if(open(R, '<', "./ng.txt")){
while(<R>){
    s/\r?\n\z//;
    next unless length $_;
    ($ng, $opt) = split(/,/);
    next unless defined $ng && length $ng;
    $opt = '' unless defined $opt;
    $ng =~ s/(\W)/\\$1/g;
    if($opt eq 'n'){
        $reload = 1 if $name =~ /$ng/;
    }elsif($opt eq 'c'){
        $reload = 1 if $comm2 =~ /$ng/;
    }else{
        $name =~ s/$ng//g;$comm =~ s/$ng//g;$mail =~ s/$ng//g;$subj =~ s/$ng//g;
    }
}
close(R);
}

if($next == 21 && !$reload){

$_ = $admin;
s/(\W)/\\$1/g;
$name =~ s/$_/＃$admin/g;

$delcrypt = &load_delcrypt;
$delcrypt ne '' && crypt($name, $delcrypt) eq $delcrypt && &del;
length($res) && undef $subj;
length($name) || &error("投稿者を記入してください");
length($comm) || &error("コメントが入力されていません");
length($res) || length($subj) || &error("題名が入力されていません");
if(my $left = &post_interval_left){
    &error("連投を検知しました。あと $left 秒ほど待ってから投稿してください");
}
&check if length($subj);

$comm =~ s/(https?\:[\w\.\~\-\/\?\+\=\:\@\%\;\#\&]+)/&url($1)/eg;
length($mail) && ($name = "<a href=\"mailto\:$mail \">$name </a>");
($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst) = localtime(time);
$month = ($mon + 1);
$year %= 100;
$year < 10 && ($year = "0$year");
$month < 10 && ($month = "0$month");
$mday < 10 && ($mday = "0$mday");
$sec < 10 && ($sec = "0$sec");
$min < 10 && ($min = "0$min");
$hour < 10 && ($hour = "0$hour");
$wday = ("日", "月", "火", "水", "木", "金", "土")[$wday];
$date = "20$year年$month月$mday日($wday)$hour時$min分$sec秒";
$res = "$year$month$mday$hour$min$sec" unless length($res);

$file = "./$folder/$res.html";
if(length($subj)){
    for($try = 0; ; $try++){
        $file = "./$folder/$res.html";
        if(!-e "./$folder/x$res.html" && !-e "./$folder/o$res.html"){
            last if sysopen(W, $file, O_RDWR|O_CREAT|O_EXCL, 0644);
            &error("スレッドを作成できません.") if !$!{EEXIST};
        }
        &error("スレッドを作成できません.") if $try >= 30;
        $res++;
    }
}else{
    open(W, '+<', $file) || &error("スレッドがありません.");
}
flock(W, 2);
$cnt = 0; $pos = 0;
while(<W>){
    last unless /<dt>(\d+)/;
    $cnt = $1;
    $pos = tell(W);
    $prev = $_;
}
seek(W, $pos, 0);
$cnt++;

if($prev =~ /<b>(.+) <\/b>.+<dd>(.+) <\/dl>/ && $name eq $1 && $comm eq $2){
    &error("二重カキコです.");
}
if(length($subj)){
    $subj1 = "<meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>$subj </title>";
    $subj2 = "<font size=+2 color=red><b>$subj </b></font>";
}
$line = <<EOL;
$subj1<dl><dt>$cnt$subj2投稿者：<font color=forestgreen><b>$name </b></font>
<font size=-1>　投稿日：$date</font><br><dd>$comm </dl>
EOL
$line =~ s/\n//g;
print W "$line\n";
$line = <<EOL;
<form method=post action=\"../$cgi\">
<input type=hidden name=\"res\" value=\"$res\">
投稿者 <input type=text name=\"name\" size=20>
メール <input type=text name=\"mail\" size=20><br>
<input type=submit value=\"↑へのレスカキコ\" >
<textarea name=\"comm\" rows=4 cols=70 ></textarea>
</form>
EOL
$line =~ s/\n//g;
print W "$line\n";
flock(W, 8);
close(W);
&touch_lasttime;

}

open(R, '<', "./blist.txt");
while(<R>){
    s/\r?\n\z//;
    ($fol, $color, $title) = split(/,/);
    last if $fol eq $folder;
}
close(R);

$page = '2' if $next != 21;
$index = "index$page.html";
$file = "./$folder/$index";
$indexfile = $file;
$main = "(メイン)" if $folder eq 'main';
($color, $bg) = split(/!/, $color);
$bg = " background=\"$bg\"" if $bg;
if ($color =~ /\*$/){
    $text = q( text="#ffffff" link="red" vlink="orange");
    chop $color;
}
$title =~ s/#c#/,/g;
$title0 = $title;
if($title0 =~ /alt=\"([^\"]+)/){
    $title0 = $1;
    undef $amezo;
}

if($next == 21 && $reload && $post_interval > 0){
    $mt = (stat($file))[9] || 0;
    &redirect if time - $mt < $post_interval;
}

if($next != 21 && $page_cache > 0 && open(P, '<', $indexfile)){
    read(P, $head, 4096);
    close(P);
    if($head =~ /<!-- AMEZO-NEXT (\d+) -->/ && $1 == $next){
        $mt = (stat($indexfile))[9] || 0;
        &redirect if time - $mt < $page_cache;
    }
}

open(LK, '>>', "./$folder/regen.lock") || &error("ロックファイルを作成できません.");
if($next == 21 && !$reload){
    $locked = eval {
        local $SIG{ALRM} = sub { die "timeout\n" };
        alarm 10;
        my $r = flock(LK, LOCK_EX);
        alarm 0;
        $r;
    };
    alarm 0;
}else{
    $locked = flock(LK, LOCK_EX | LOCK_NB);
}
&redirect unless $locked;
$tmpfile = "$indexfile.tmp$$";
open(W, '>', $tmpfile) || &error("indexを書き込めません.");

print W <<EOF;
<html><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title0$amezo$main</title></head>
$copyright
<!-- AMEZO-NEXT $next -->
<body$bg bgcolor="$color"$text>
<font size=+2>$title$amezo$main</font>
EOF

open(R, '<', "./header.html");
print W while <R>;
close(R);

print W <<EOF;
<form method=post action="../$cgi">
<input type=submit value="次のページ">
<input type=text name="next" value="$next" size="3">〜</form>
<font size=2>
<!-- HEAD LINE -->
EOF

opendir(DIR, "./$folder");
@dir = readdir(DIR);
closedir(DIR);

for($i=0;$i<@dir;$i++){
    $ts = ($dir[$i] =~ /^\d+\.html/) ?
        sprintf('%011d', (stat("./$folder/$dir[$i]"))[9] || 0) : ' ';
    $dir[$i] = "$ts,$dir[$i]";
}
@dir = sort {$b cmp $a;} @dir;

if($max_threads > 0){
    $n_thr = 0;
    @dir = grep {
        my $keep = 1;
        if($_ !~ /^ ,/ && ++$n_thr > $max_threads){
            my ($t_ts, $t_file) = split(/,/, $_, 2);
            if(!-e "./$folder/o$t_file" && rename("./$folder/$t_file", "./$folder/o$t_file")){
                $keep = 0;
            }
        }
        $keep;
    } @dir;
}

for($i=$next - 21;$i < $next+100-21 && $dir[$i];$i++){
    ($ts, $file) = split(/,/, $dir[$i]);
    last if $ts eq ' ';
    open(R, '<', "./$folder/$file");
    $_ = <R>;
    close(R);
    $ttl = (defined $_ && /<b>([^\<]*)<\/b>/) ? $1 : '';
    $j = $i+1;
    print W " $j<a href=\"$file\">$ttl</a>\n";
}
print W "<!-- HEAD LINE END -->\n</font>\n";

for($i=$next - 21;$i < $next - 1 && $dir[$i];$i++){
    ($ts, $file) = split(/,/, $dir[$i]);
    last if $ts eq ' ';
    print W <<EOL;
<table border=1 cellspacing=1 cellpadding=2 width=100% bgcolor="#efefef"><tr><td><font color="#000000">
EOL
    open(R, '<', "./$folder/$file");
    $_ = <R>;
    s/<meta[^>]*>//g;
    s/<title>.*<\/title>//;
    print W;
    for($j=0;<R>;$j++){
        last if /^<form/;
        $lines[$j % 7] = $_;
    }
    close(R);
    $k = $j - 7;
    $k = 0 if $k < 0;
    for($m=$k;$m < $j;$m++){
        print W "$lines[$m % 7]";
    }
    $file =~ /(.+)\.html/;
    $res = $1;
    undef $sub;
    $k = $j - 98;
    $sub = <<EOL if $j > 200;
<input type=text name="from" value="$k" size="4">
<input type=submit name="sub" value="\x{301c}">
EOL
    print W <<EOL;
</font></td></tr></table>
<form method=post action="../$cgi">
<input type=hidden name="res" value="$res">
投稿者 <input type=text name="name" size=20>
メール <input type=text name="mail" size=20><br>
<input type=submit value="↑へのレスカキコ" >
<a href="$res.html">レス全部を見る</a>
$sub<a href="#top">上へ　</a><a href="./">リロード</a><br>
<textarea name="comm" rows=4 cols=70 ></textarea>
</form><hr>
EOL

}

print W <<EOF;
<form method=post action="../$cgi">
<input type=submit value="次のページ">
<input type=text name="next" value="$next" size="3">〜</form>
<hr></body></html>
EOF

close(W) || do { unlink($tmpfile); &error("indexを書き込めません."); };
$mode = ((stat($indexfile))[2] || 0644) & 07777;
chmod($mode, $tmpfile);
rename($tmpfile, $indexfile) || do { unlink($tmpfile); &error("indexを更新できません."); };
flock(LK, LOCK_UN);
close(LK);
&redirect;

sub redirect {
    undef $index unless $page;
    my $target = "$urlbase$folder/$index";
    print "Location: $target\r\n";
    print "Content-type: text/html; charset=UTF-8\r\n\r\n";
    print "<html><head><meta http-equiv=\"refresh\" content=\"0;url=$target\"></head>";
    print "<body>Redirecting to <a href=\"$target\">$folder/$index</a>...</body></html>";
    exit;
}

sub url {
    $url = $dsp = $_[0];
    $url =~ s/&amp;/&/g;
    "<a href=\"$url\" target=\"_blank\" rel=\"noopener noreferrer nofollow\">$dsp</a>";
}

sub error {
    print "Content-type: text/html; charset=UTF-8\r\n\r\n<html><head>"
        . '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        . "</head><body><h2>$_[0]</h2></body></html>";
    exit;
}

sub res {
    my $from = $form{'from'};
    $from = 1 unless defined $from && $from =~ /^\d+$/;
    open(R, '<', "./$folder/$res.html") || &error("スレッドがありません.");
    print "Content-type: text/html; charset=UTF-8\r\n\r\n";
    print <R>."";
    while(<R>){
        last if /<dt>(\d+)/ && $1 >= $from;
    }
    print if defined $_;
    $fol = "<input type=hidden name=\"folder\" value=\"$folder\">";
    while(<R>){
        if(/<\/form>/){
            s/(action=")\.\.\//$1/;
            s/(<\/form>)/$fol$1/;
        }
        print;
    }
    exit;
}

sub del {
    my $delcrypt = &load_delcrypt;
    if(length($mail)){
        $name = $admin;
        undef $mail unless $mail =~ /\@/;
        return;
    }
    unless(length($res)){
        print "Content-type: text/html; charset=UTF-8\r\n\r\n";
        exit;
    }
    $file = "./$folder/$res.html";
    if(!length($comm)){
        $newfile = "./$folder/x$res.html";
        rename($file, $newfile) || &error("スレッドがありません.");
        chmod 0600, $newfile;
        print "Content-type: text/html; charset=UTF-8\r\n\r\n";
        exit;
    }
    $comm = ',' . $comm . ',';
    $comm =~ s/(\d{1,9})\-(\d{1,9})/&expand_range($1, $2)/eg;
    $comm =~ s/<br>/,/g;
    open(W, '+<', $file) || &error("スレッドがありません.");
    flock(W, LOCK_EX);
    $orig_mtime = (stat(W))[9];
    @lines = <W>;
    @keep = ();
    @stamp = ();
    foreach(@lines){
        next if /<dt>(\d+)/ && ($n=','.$1.',') && $comm =~ /$n/;
        # 不可視文字を除去し、綺麗に日本語の日付フォーマットをキャプチャする正規表現に修正
        if (/(\d{4})年(\d{2})月(\d{2})日\([^)]+\)(\d{2})時(\d{2})分(\d{2})秒/) {
            @stamp = ($1, $2, $3, $4, $5, $6);
        }
        push(@keep, $_);
    }
    seek(W, 0, 0);
    truncate(W, 0);
    print W @keep;
    close(W);
    $ts = $orig_mtime;
    if(@stamp){
        $t = eval { Time::Local::timelocal($stamp[5], $stamp[4], $stamp[3], $stamp[2], $stamp[1] - 1, $stamp[0]) };
        $ts = $t if defined $t;
    }
    $ts -= $1*24*60*60 if $comm =~ /p(\d+)/;
    utime $ts+1, $ts+1, $file;
    chmod 0404, $file if $comm =~ /x/;
    print "Content-type: text/html; charset=UTF-8\r\n\r\n";
    exit;
}

sub expand_range {
    my ($lo, $hi) = @_;
    $hi = $lo + 1000 if $hi - $lo > 1000;
    return $lo <= $hi ? join(',', $lo .. $hi) : '';
}

sub check {
    $ip = &client_ip;
    $time = time;
    sysopen(Z, 'global.mem', O_RDWR|O_CREAT, 0600) || return;
    flock(Z, LOCK_EX);
    %iplist = ();
    while(<Z>){
        s/\r?\n\z//;
        ($ip_o, $time_o) = split;
        $iplist{$ip_o} = $time_o if defined $time_o && $time - $time_o < 180;
    }
    if($iplist{$ip}){
        $remain = 180 - ($time - $iplist{$ip});
        close(Z);
        &error("あと " . $remain . "秒たってから投稿してください");
    }
    $iplist{$ip} = $time;
    seek(Z, 0, 0);
    truncate(Z, 0);
    for (keys %iplist){
        print Z "$_ $iplist{$_}\n";
    }
    close(Z);
}

sub client_ip {
    my $addr = $ENV{'REMOTE_ADDR'};
    $addr = '' unless defined $addr;
    return $addr unless grep { $_ eq $addr } @trusted_proxies;
    my $c = $ENV{'HTTP_CF_CONNECTING_IP'};
    if(!defined $c || $c !~ /^[0-9A-Fa-f:.]{2,45}$/){
        my @f = split(/\s*,\s*/, $ENV{'HTTP_X_FORWARDED_FOR'} || '');
        $c = $f[-1];
    }
    return (defined $c && $c =~ /^[0-9A-Fa-f:.]{2,45}$/) ? $c : $addr;
}

sub decode_bytes {
    my ($enc, $str) = @_;
    my $src = $str;
    my $dec;
    return $dec if eval { $dec = Encode::decode($enc, $src, Encode::FB_CROAK()); 1 };
    return undef;
}

sub to_utf8 {
    my ($str) = @_;
    return '' unless defined $str;
    my $dec = &decode_bytes('UTF-8', $str);
    $dec = &decode_bytes('cp932', $str) unless defined $dec;
    return Encode::encode('UTF-8', $dec) if defined $dec;
    (my $out = $str) =~ s/[\x80-\xff]//g;
    return $out;
}

sub html_chars {
    my ($str) = @_;
    my $dec = &decode_bytes('UTF-8', $str);
    return undef unless defined $dec;
    my @c = ($dec =~ /(&(?:amp|quot|lt|gt|#39);|[\s\S])/g);
    return \@c;
}

sub clen {
    my ($str) = @_;
    return 0 unless defined $str;
    my $c = &html_chars($str);
    return defined $c ? scalar(@$c) : length($str);
}

sub ccut {
    my ($str, $max) = @_;
    return '' unless defined $str;
    my $c = &html_chars($str);
    return substr($str, 0, $max) unless defined $c;
    splice(@$c, $max) if @$c > $max;
    return Encode::encode('UTF-8', join('', @$c));
}

sub load_delcrypt {
    local $/;
    open(my $f, '<', $delcryptfile) or return '';
    my $c = <$f>;
    close($f);
    return '' unless defined $c;
    $c =~ s/^\s+//;
    $c =~ s/\s+$//;
    return $c;
}

sub is_board {
    my ($f) = @_;
    local $_;
    open(my $b, '<', './blist.txt') or return 0;
    while(<$b>){
        s/\r?\n\z//;
        my ($n) = split(/,/);
        if(defined $n && $n eq $f){ close($b); return 1; }
    }
    close($b);
    return 0;
}

sub strip_invisible {
    my ($str) = @_;
    my $dec = &decode_bytes('UTF-8', $str);
    return $str unless defined $dec;
    $dec =~ s/[\x{0080}-\x{009F}\x{00AD}\x{034F}\x{061C}\x{115F}\x{1160}\x{17B4}\x{17B5}\x{180B}-\x{180E}\x{200B}\x{200C}\x{200E}\x{200F}\x{2028}-\x{202E}\x{2060}-\x{206F}\x{3164}\x{FEFF}\x{FFA0}\x{FFF9}-\x{FFFB}]//g;
    return Encode::encode('UTF-8', $dec);
}

sub post_interval_left {
    return 0 unless $post_interval > 0;
    my $last = 0;
    if(open(my $k, '<', "./$folder/lasttime.txt")){
        $last = <$k>;
        close($k);
    }
    $last = 0 unless defined $last && $last =~ /^\d+$/;
    my $left = $post_interval - (time - $last);
    return $left > 0 ? $left : 0;
}

sub touch_lasttime {
    return unless $post_interval > 0;
    open(my $k, '>', "./$folder/lasttime.txt") or return;
    print $k time;
    close($k);
}
