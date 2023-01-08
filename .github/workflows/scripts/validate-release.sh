#!/usr/bin/env bash
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This script is used to validate the release package, including:
# 1. Check the release package name & content
# 3. Check the release package sha512
# 4. Check the release package gpg signature

URL_PREFIX="https://dist.apache.org/repos/dist/dev/incubator/hugegraph/"
# release version (input by committer)
RELEASE_VERSION=$1
JAVA_VERSION=$2
# git release branch (check it carefully)
#GIT_BRANCH="release-${RELEASE_VERSION}"

RELEASE_VERSION=${RELEASE_VERSION:?"Please input the release version behind script"}

# step1: download svn files
rm -rf dist/"$RELEASE_VERSION" && svn co ${URL_PREFIX}/"$RELEASE_VERSION" dist/"$RELEASE_VERSION"
cd dist/"$RELEASE_VERSION" || exit

# step2: check environment & import public keys
shasum --version 1>/dev/null || exit
gpg --version 1>/dev/null || exit

wget https://downloads.apache.org/incubator/hugegraph/KEYS
gpg --import KEYS || exit
# TODO: how to trust all public keys once?

# step3: check sha512 & gpg signature
for i in *.tar.gz; do
  echo "$i"
  shasum -a 512 --check "$i".sha512 || exit
done

for i in *.tar.gz; do
  echo "$i"
  eval gpg "${GPG_OPT}" --verify "$i".asc "$i" || exit
done

# step4: validate source packages
for i in *src.tar.gz; do
  echo "$i"
  #### step4.0: check the directory include "incubating"
  if [[ ! "$i" =~ "incubating" ]]; then
    echo "The package name should include incubating" && exit 1
  fi
  # TODO: remove the case when dir bug fix
  if [[ $i == "apache-hugegraph-incubating-1.0.0-src.tar.gz" ]]; then
    mkdir ./apache-hugegraph-incubating-1.0.0-src
    tar xzf "$i" -C ./apache-hugegraph-incubating-1.0.0-src --strip-components 1 || exit
  else
    tar xzf "$i" || exit
  fi
  cd "$(basename "$i" .tar.gz)" || exit

  #### step4.1: check the directory include "NOTICE" and "LICENSE" file
  if [[ ! -f "LICENSE" ]]; then
    echo "The package should include LICENSE file" && exit 1
  fi
  if [[ ! -f "NOTICE" ]]; then
    echo "The package should include NOTICE file" && exit 1
  fi

  #### step4.2: compile the packages
  # skip compute module in java8
  if [[ $JAVA_VERSION == 8 && "$i" =~ "computer" ]]; then
    continue
  fi
  mvn clean package -DskipTests -ntp || exit
  cd .. || exit
done

#### step4.3: run the compiled packages in server
cd ./*hugegraph-incubating*src/*hugegraph*"${RELEASE_VERSION}" || exit
bin/init-store.sh && sleep 1
bin/start-hugegraph.sh || exit
sleep 5
bin/stop-hugegraph.sh
cd .. || exit

#### step4.4: run the compiled packages in toolchain (include loader/tool/hubble)
cd ./*hugegraph-toolchain*src/*hugegraph*"${RELEASE_VERSION}" || exit
# loader

# step5: validate the binary packages
#### step5.0: check the directory include "incubating"
#### step5.1: check the directory include "NOTICE" and "LICENSE" file
#### step5.4: run the binary packages

echo "Finish validate, please check all steps manually again!"
