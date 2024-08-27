import subprocess as sp
import logging
import pandas as pd

def run_kestrel(kmer_command, vcf_path, output, fastq_1, fastq_2, reference_VNTR):
    if vcf_path.is_file():
        print("VCF file already exists...")
        return None
    else:
        if None not in (fastq_1, fastq_2, reference_VNTR):
            print("Launching Kestrel...with Kmer size 20!\n")
            process = sp.Popen(kmer_command, shell=True)
            process.wait()
            logging.info('Mapping-free genotyping of MUC1-VNTR with kmer size 20 done!')
        else:
            print(f"{fastq_1} is not an expected fastq file... skipped...")
            return None

def process_kmer(kmer_data):
    # processing logic for kmer results...
    pass

# Other related Kestrel functions go here
