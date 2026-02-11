#!/bin/bash
###############################
# release notes:
# Version 1.00 [20250405] code name hermes
# Version 1.01 [20250508] update sanity checks; adding attributes: 'clms-upload-version,odp-priority'; adjust date with time zone UTC
# Version 1.02 [20250709] addition of rclone --s3-disable-http2 flag to resolve potential problems with GO and http2 https://forum.rclone.org/t/disable-http2-in-conf-file/49149
# Version 1.03 [20250718] better handling of rclone error codes
# Version 1.04 [20250723] rename "last_modified" attribute which denotes modification time in CLMS producer local storage to "created"
# Version 1.05 [20250827] proper handling of whitespaces in local path
# Version 1.06 [20250901] update of the UTM zone of uploaded products, reformating of rclone command, addition of the --retries-sleep --tps-limit to rclone 
# Version 1.07 [20251111] change .tar support logic, add product priority based on temporalRepeatRate, EEA versioning support, fallback to WAW3-2 region if WAW3-1 is unavailable 
# Version 1.08 [20251113] add sanity check to verify if the jq, gdal, wget, rclone utilities are installed 
###############################
version="1.08"
usage()
{
cat << EOF
#This utility copies the CLMS products to CDSE staging storage.
#IMPORTANT! First export environmental variables!!!!
export RCLONE_CONFIG_CLMS_TYPE=s3
export RCLONE_CONFIG_CLMS_ACCESS_KEY_ID=YOUR_CLMS_PUBLIC_S3_KEY
export RCLONE_CONFIG_CLMS_SECRET_ACCESS_KEY=YOUR_CLMS_PRIVATE_S3_KEY
export RCLONE_CONFIG_CLMS_REGION=default
export RCLONE_CONFIG_CLMS_ENDPOINT='https://s3.waw3-1.cloudferro.com'
export RCLONE_CONFIG_CLMS_PROVIDER='Ceph'
#
# example of usage
#
# Single file upload:
clms_upload.sh -b CLMS-YOUR-BUCKET-NAME -l "/tmp/c_gls_NDVI_200503110000_GLOBE_VGT_V3.0.1.nc"
#
#Batch upload of all NetCDF files residing localy in /home/ubuntu directory in 5 parallel sessions:
find /home/ubuntu -name "*.nc" | xargs -l -P 5 bash -c 'clms_upload.sh -b CLMS-YOUR-BUCKET-NAME -l "$0"'
#
################################################################
OPTIONS:
   -b	   [REQUIRED] bucket name to upload to specific to a producer e.g. CLMS-YOUR-BUCKET-NAME
   -h      this message
   -l      [REQUIRED] local path (i.e. file system) path to input file or a directory with CLMS product name containing product files (e.g. COGs & STAC JSON metadata) 
   -o      [OPTIONAL] shall input file in the CLMS-YOUR-BUCKET-NAME bucket in the CDSE staging storage be overwritten?
   -p      [OPTIONAL] job priority ranging 0-9. Higher priority indicates that a CLMS product will be ingested faster. Default 3.  
   -r      [OPTIONAL] product name(s) to be replaced/patched by the product to uploaded. 
		   If more than one product needs to be replaced (e.g. NetCDF & COGs) than comma-separated list of names should be provided.
   -v      clms_upload.sh version
EOF
}
while getopts “b:l:p:r:hov” OPTION; do
	case $OPTION in
		b)
			bucket=$OPTARG
			;;
		h)
			usage
			exit 0
			;;
		l)
			local_file="${OPTARG%/}"
			;;
		o)  
			overwrite=' --no-check-dest'
			;;
		p)
			priority=$OPTARG
			;;
		r)  
			rep=$OPTARG
			;;
		v)
			echo version $version
			exit 0
			;;
		?)
			usage
			exit 1
			;;
	esac
done
#########################################sanity checks
#verify if jq, gdal, wget, rclone utilities are installed
if ! [[ $(which jq) && $(which gdalinfo) && $(which ogrinfo) && $(which wget) && $(which rclone) ]]; then
	echo "ERROR: jq AND gdalinfo AND wget AND rclone utilities must be installed!"
	exit 1
fi
#verify if bucket was set
if [ -z $bucket ]; then
	echo "ERROR: Bucket name must be specified!"
	exit 2
fi
#verify if local_file was set
if [ -z "$local_file" ]; then
	echo "ERROR: Local path must be specified!"
	exit 3
fi
#verify if environmental variables were set
if [ -z $RCLONE_CONFIG_CLMS_TYPE ] || [ -z $RCLONE_CONFIG_CLMS_ACCESS_KEY_ID ] || [ -z $RCLONE_CONFIG_CLMS_SECRET_ACCESS_KEY ] || [ -z $RCLONE_CONFIG_CLMS_REGION ] || [ -z $RCLONE_CONFIG_CLMS_ENDPOINT ] || [ -z $RCLONE_CONFIG_CLMS_PROVIDER ]; then
	echo "ERROR: Some environmental variables starting with RCLONE_CONFIG_CLMS_XXXXX were not set!"
	exit 4
fi
#verify if file exists in the local storage
if [ ! -f "$local_file" ] ; then
    echo "ERROR: File $local_file does not exist in the local storage!"
    exit 5
fi

#verify if the product exists already in the CDSE OData 
odata_product_count=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename "${local_file%.*}")'%27))' | jq '.value | length')
if [ $odata_product_count -gt 0 ]; then
	echo 'ERROR: Such product exists in the CDSE!'
	exit 6
fi
#verify if the product has not been already deleted from the CDSE
deleted_product_count=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/DeletedProducts?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename "${local_file%.*}")'%27))' | jq '.value | length')
if [ $deleted_product_count -gt 0 ]; then
	echo 'ERROR: Such product has been deleted from the CDSE!'
	exit 7
fi
#verify if the product to be uploaded is readable by gdal
if [ "${local_file##*.}" == "tar" ]; then
    #add test if tar has a correctly named internal directory
    if [ $(tar tf $local_file | grep -c "./$(basename ${local_file%.*})/") -eq 0 ]; then
        echo "ERROR: TAR file does not contain the folder named '$(basename ${local_file%.*})' containing all the product files."
        exit 8
    fi
	for gdal_product in $(tar tf $local_file | grep -E '.tif|.nc' | grep -vE '.tif.|.nc.' |  cut -c3- | sed "s|^|${local_file}/|"); do
		echo $gdal_product
		gdalinfo /vsitar/${gdal_product} > /dev/null 2>&1
		if [ $? -ne 0 ]; then
			ogrinfo /vsitar/${gdal_product} > /dev/null 2>&1
			if [ $? -ne 0 ]; then
				echo "ERROR: GDAL can not open ${gdal_product}!"
				exit 9
			fi
		fi
	done
else
	gdalinfo "$local_file" > /dev/null 2>&1
	if [ $? -ne 0 ]; then
		ogrinfo "$local_file" > /dev/null 2>&1
		if [ $? -ne 0 ]; then
			echo "ERROR: GDAL can not open ${local_file}!"
			exit 10
		fi
	fi
fi

#verify if json STAC file within a .tar product is valid
if [ "${local_file##*.}" == "tar" ]; then
	for stac_json in $(tar tf $local_file | grep '_stac.json'); do
		tar -xOf $local_file $stac_json | jq . >/dev/null 2>&1
		if [ $? -ne 0 ]; then
			echo "ERROR: STAC JSON is invalid. Can not parse ${stac_json}!"
            exit 11
		fi
	done
fi
#check JRC product versioning
if [ $(basename "$local_file" | tr -dc '.' | wc -c) -eq 3 ]; then
	#verify path number is not 0
	patch_number=$(echo "$local_file" | rev | cut -f 2 -d '.' | tr -cd '0-9')
	if [ $patch_number -eq 0 ]; then
		echo "ERROR: Patch number (i.e. last digit in version) has to start with 1"
		exit 12
	fi
	#verify if the previous product version exists
	odata_product=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename "$local_file" | rev | cut -f 3- -d '.' | rev)'%27))&$orderby=Name%20desc')
	if [ $(printf "$odata_product" | jq '.value|length') -gt 0 ]; then
		if [ -z $rep ]; then
			product_to_replace=$(printf "$odata_product" |  jq -r '.value[].Name' | paste -sd, -)
		fi
		odata_patch_number=$(printf "$odata_product" |  jq '.value[0].Name' | rev | cut -f 1 -d '.' | tr -dc '0-9')
		if [ $patch_number -ne $((${odata_patch_number}+1)) ]; then
			echo "ERROR: Patch version in CDSE is ${odata_patch_number} and the patch number of the product uploaded product should be $((${odata_patch_number}+1)) but it is ${patch_number}!"
			exit 13
		fi
	fi
fi
#check EEA product versioning
if [[ "$local_file" =~ ^.*V[0-9]{3}$ ]]; then
	patch_number=$(echo ${local_file%.*} | tail -c 3 | bc)
	#verify if the previous product version exists
	odata_product=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename "${local_file%.*}" | sed "s/..$//")'%27))&$orderby=Name%20desc')
	if [ $(printf "$odata_product" | jq '.value|length') -gt 0 ]; then
		if [ -z $rep ]; then
			product_to_replace=$(printf "$odata_product" |  jq -r '.value[].Name' | paste -sd, -)
		fi
		odata_patch_number=$(printf "$odata_product" |  jq '.value[0].Name' | tail -c 3 | bc)
		if [ $patch_number -ne $((${odata_patch_number}+1)) ]; then
			echo "ERROR: Patch version in CDSE is ${odata_patch_number} and the patch number of the product uploaded product should be $((${odata_patch_number}+1)) but it is ${patch_number}!"
			exit 14
		fi
	fi
fi

#verify product to replace
if [ ! -z $rep ]; then
	rep_product=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename $rep | rev | cut -f 2- -d '.' | rev)'%27))')
	if [ $(printf "$rep_product" | jq '.value|length') -gt 0 ]; then
		product_to_replace=$(printf "$rep_product" |  jq -r '.value[].Name' | paste -sd, -)
	else
		echo "ERROR: Product to be patched does not exist in the CDSE: $rep"
		exit 15
	fi
fi

#print products to be replaced
if [ ! -z $product_to_replace ]; then
	echo "INFO: Following products will be patched in the CDSE: $product_to_replace"
fi

#try to set ingestion priority for the product to be uploaded
if [ -z "$priority" ]; then
    priority=3
    odata_product=$(wget -qO - 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=(Collection/Name%20eq%20%27CLMS%27%20and%20startswith(Name,%27'$(basename "${local_file}" | cut -f 1-3 -d "_")'%27))&$top=1&$expand=Attributes')
    temporalRepeatRate=$(echo "$odata_product" | jq -r '.value[].Attributes[] | select(.Name=="temporalRepeatRate") | .Value')
    case "$temporalRepeatRate" in
        hourly)
            priority=7
            ;;
        daily)
            priority=5
            ;;
    esac
fi
#check if https://s3.waw3-1.cloudferro.com if not fall back to https://s3.waw3-2.cloudferro.com
wget -q --spider "$RCLONE_CONFIG_CLMS_ENDPOINT" || RCLONE_CONFIG_CLMS_ENDPOINT='https://s3.waw3-2.cloudferro.com'

#extract technical attributes for S3
last_modified=$(date -u -r "$local_file" '+%Y-%m-%dT%H:%M:%SZ')
s3_path=${bucket}$(date -u --date now '+/%Y/%m/%d')
timestamp=$(date -u -d now '+%Y-%m-%dT%H:%M:%SZ')
file_size=$(du -smb --apparent-size "$local_file" | cut -f1)
md5_checksum=$(md5sum -b "$local_file" | cut -c-32)

#upload product
rclone -q copy \
--s3-disable-http2 \
--s3-no-check-bucket \
--retries=20 \
--retries-sleep=1s \
--low-level-retries=20 \
--tpslimit=5 \
--checksum \
--s3-use-multipart-uploads='false' \
--metadata \
--metadata-set odp-priority=$priority \
--metadata-set clms-upload-version=$version \
--metadata-set uploaded=$timestamp \
--metadata-set WorkflowName="clms_upload" \
--metadata-set source-s3-endpoint-url=$RCLONE_CONFIG_CLMS_ENDPOINT \
--metadata-set file-size=$file_size \
--metadata-set md5=$md5_checksum \
--metadata-set created=$last_modified \
--metadata-set s3-public-key=${RCLONE_CONFIG_CLMS_ACCESS_KEY_ID} \
--metadata-set source-s3-path='s3://'${s3_path} \
--metadata-set source-cleanup=true \
--metadata-set product-to-replace=${product_to_replace}${overwrite} "$local_file" CLMS:$s3_path
rclone_exit_code=$?
if [ $rclone_exit_code != 0 ]; then
	echo "ERROR: rclone exit code:$rclone_exit_code. Failed to upload $local_file to s3://${s3_path}"
 	exit 16
else 
	echo "SUCCESS: Uploaded $local_file to s3://${s3_path} in ${RCLONE_CONFIG_CLMS_ENDPOINT} endpoint!"
 	exit 0
fi
